// Этап 1.7.h — LoRa Serial канал, full-duplex PHY-калиброванная модель.
//
// Buchstabe ТЗ: "LoRa через Serial Port" — MAVLink-байтстрим между host
// orchestrator и SITL ходит через виртуальную LoRa-радио-петлю, описанную
// параметрами реального SX1276 modem'а (SF, BW, distance, PER). Никакого
// IP-stack в радио-петле нет — только PTY с двух сторон.
//
// Архитектура (симметричный P2P):
//
//   GCS  PtyApp  ←→  PointToPointNetDevice  ←→  PointToPointChannel  ←→
//        polling /tmp/ptyGCS_lora                rate=5470 bps (SF7/BW125)
//        Send raw bytes как Packet              delay≈50ms (per-packet airtime)
//        Receive→write to PTY                   RateErrorModel(packet) p=PER(d)
//
//   ↔ симметрично ↔
//
//   UAV  PtyApp  ←→  PointToPointNetDevice  ←→  (тот же канал)
//        polling /tmp/ptyUAV_lora
//
// Оба узла identical EndDevices — full-duplex (NetDevice::Send в любой момент,
// receive callback пишет в PTY). Это и нужно для mission AUTO upload через
// LoRa: orchestrator MISSION_COUNT/ITEM/ARM/MISSION_START идут к SITL, а
// телеметрия идёт обратно — всё через один радио-канал.
//
// Параметры SX1276 рассчитываются из ITU-R LoRa airtime формулы:
//   T_symbol  = 2^SF / BW
//   T_preamble = (N_preamble + 4.25) × T_symbol
//   N_payload_symbols = 8 + max(ceil((8·PL - 4·SF + 28 + 16) / (4·SF)) · (CR+4), 0)
//   T_packet = T_preamble + N_payload_symbols × T_symbol
//   bit_rate ≈ SF × (4/(4+CR)) / T_symbol
//
// PER (packet error rate) при distance=1000м для SF7/BW125 в open-field
// деградации составляет ~1% (Bor et al. 2016, Augustin et al. 2016).
// Для distance > 5km PER растёт экспоненциально.
//
// Альтернативный полностью PHY-correct baseline остаётся в lora_serial_lorawan.cc
// (signetlabdei lorawan ED Class A + GW, ITU-R RP.452, LoRa modulation),
// он валиден для telemetry uplink-only демонстраций.

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"
#include "ns3/error-model.h"

#include <fcntl.h>
#include <unistd.h>

#include <cerrno>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("LoraSerialScenario");

// -----------------------------------------------------------------------------
// JSONL log.
// -----------------------------------------------------------------------------
static std::ofstream g_jsonl;
static std::string g_run_id = "dev";

static void
emit_event(const std::string& line) {
    if (g_jsonl.is_open()) {
        g_jsonl << line << "\n";
        g_jsonl.flush();
    }
}

// -----------------------------------------------------------------------------
// Per-side trace counters.
// -----------------------------------------------------------------------------
struct SideStats {
    uint64_t packets_sent = 0;
    uint64_t packets_received = 0;
    uint64_t bytes_sent = 0;
    uint64_t bytes_received = 0;
};

static SideStats g_uav;
static SideStats g_gcs;

// -----------------------------------------------------------------------------
// SX1276 PHY-калибровка: вычислить data_rate и airtime для SF/BW/CR.
// Reference: Semtech SX1276 datasheet rev.7 (2019), Table 12 "LoRa Modem Properties".
// Также Bor et al. 2016 "Do LoRa Low-Power Wide-Area Networks Scale?".
// -----------------------------------------------------------------------------
struct LoraPhyParams {
    double data_rate_bps;
    double airtime_ms;
    std::string sx1276_table_entry;  // Например "SF7/BW125 — 5470 bps"
};

static LoraPhyParams
calibrate_sx1276(uint32_t sf, double bandwidth_hz, double coding_rate_denom) {
    double T_symbol = std::pow(2.0, sf) / bandwidth_hz;
    // bit_rate = SF × (4/CR_denom) / T_symbol
    double bit_rate = static_cast<double>(sf) * (4.0 / coding_rate_denom) / T_symbol;

    // Airtime для типичного MAVLink packet (~64 bytes payload + header).
    constexpr uint32_t PL = 64;
    constexpr uint32_t N_preamble = 8;
    constexpr uint32_t H = 0;
    constexpr uint32_t DE = 0;
    double T_preamble_ms = (N_preamble + 4.25) * T_symbol * 1000.0;
    double payload_symbols = 8.0 + std::max(
        std::ceil((8.0 * PL - 4.0 * sf + 28.0 + 16.0 - 20.0 * H) /
                  (4.0 * (sf - 2.0 * DE))) * coding_rate_denom,
        0.0);
    double airtime_ms = T_preamble_ms + payload_symbols * T_symbol * 1000.0;

    LoraPhyParams p;
    p.data_rate_bps = bit_rate;
    p.airtime_ms = airtime_ms;
    std::ostringstream o;
    o << "SF" << sf << "/BW" << static_cast<int>(bandwidth_hz / 1000) << " — "
      << static_cast<int>(bit_rate) << " bps, airtime≈" << std::fixed
      << std::setprecision(1) << airtime_ms << "ms";
    p.sx1276_table_entry = o.str();
    return p;
}

// PER (packet error rate) от distance для open-field SF7/BW125.
// Калибровка по Augustin et al. 2016, Table III. Path-loss exponent 3.76.
static double
per_for_distance(double distance_m, uint32_t sf) {
    double d_eff = distance_m / std::pow(2.0, static_cast<int>(sf) - 7);
    if (d_eff < 500.0) return 0.0;
    if (d_eff < 1500.0) return 0.01;
    if (d_eff < 3500.0) return 0.05;
    if (d_eff < 5500.0) return 0.20;
    if (d_eff < 10000.0) return 0.60;
    return 0.99;
}

// -----------------------------------------------------------------------------
// PtyApp — читает host PTY (через socat UNIX-CONNECT bridge) и шлёт байты
// через NetDevice. Receive side пишет байты в свой PTY.
// -----------------------------------------------------------------------------
static std::string g_pty_uav_path = "";
static std::string g_pty_gcs_path = "";

class PtyApp : public Application {
public:
    static TypeId GetTypeId() {
        static TypeId tid = TypeId("PtyApp").SetParent<Application>().AddConstructor<PtyApp>();
        return tid;
    }

    PtyApp() : m_master_fd(-1) {}
    ~PtyApp() override {
        if (m_master_fd >= 0) close(m_master_fd);
    }

    void Configure(const std::string& pty_path,
                   const std::string& side,
                   Ptr<NetDevice> dev) {
        m_pty_path = pty_path;
        m_side = side;
        m_dev = dev;
    }

    // NetDevice ReceiveCallback → пишем bytes в свой PTY.
    bool OnReceive(Ptr<NetDevice> /*dev*/, Ptr<const Packet> packet,
                   uint16_t /*proto*/, const Address& /*from*/) {
        if (m_master_fd < 0) return false;
        std::vector<uint8_t> buf(packet->GetSize());
        packet->CopyData(buf.data(), buf.size());
        ssize_t w = write(m_master_fd, buf.data(), buf.size());
        SideStats& s = (m_side == "uav") ? g_uav : g_gcs;
        s.packets_received += 1;
        s.bytes_received += buf.size();

        std::ostringstream o;
        o << "{\"event_type\":\"component\",\"component\":\"ns3:lorawan\","
          << "\"phase\":\"pty_write\",\"side\":\"" << m_side << "\","
          << "\"sim_time\":" << Simulator::Now().GetSeconds()
          << ",\"bytes\":" << w << ",\"run_id\":\"" << g_run_id << "\"}";
        emit_event(o.str());
        return true;
    }

private:
    void StartApplication() override {
        m_master_fd = open(m_pty_path.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
        if (m_master_fd < 0) {
            NS_LOG_UNCOND("[pty " << m_side << "] open(" << m_pty_path << ") failed: "
                          << strerror(errno) << " (запустил ли ты socat ДО ns-3?)");
            return;
        }
        NS_LOG_UNCOND("[pty " << m_side << "] opened " << m_pty_path
                      << " fd=" << m_master_fd);

        std::ostringstream o;
        o << "{\"event_type\":\"component\",\"component\":\"ns3:lorawan\","
          << "\"phase\":\"pty_open\",\"side\":\"" << m_side << "\","
          << "\"pty_path\":\"" << m_pty_path << "\","
          << "\"sim_time\":" << Simulator::Now().GetSeconds()
          << ",\"run_id\":\"" << g_run_id << "\"}";
        emit_event(o.str());

        // 1.7.h: оба side'а делают PollAndSend — full-duplex.
        Simulator::Schedule(MilliSeconds(10), &PtyApp::PollAndSend, this);
    }

    void StopApplication() override {
        if (m_master_fd >= 0) {
            close(m_master_fd);
            m_master_fd = -1;
        }
    }

    void PollAndSend() {
        if (m_master_fd < 0) return;
        uint8_t buf[1024];
        ssize_t n = read(m_master_fd, buf, sizeof(buf));
        if (n > 0) {
            Ptr<Packet> packet = Create<Packet>(buf, n);
            if (m_dev) {
                // PointToPointNetDevice::Send: broadcast address, ethertype = local-experimental.
                m_dev->Send(packet, m_dev->GetBroadcast(), 0x88B5);
            }
            SideStats& s = (m_side == "uav") ? g_uav : g_gcs;
            s.packets_sent += 1;
            s.bytes_sent += n;

            std::ostringstream o;
            o << "{\"event_type\":\"component\",\"component\":\"ns3:lorawan\","
              << "\"phase\":\"pty_read\",\"side\":\"" << m_side << "\","
              << "\"sim_time\":" << Simulator::Now().GetSeconds()
              << ",\"bytes\":" << n << ",\"run_id\":\"" << g_run_id << "\"}";
            emit_event(o.str());
        }
        Simulator::Schedule(MilliSeconds(10), &PtyApp::PollAndSend, this);
    }

    int m_master_fd;
    std::string m_pty_path;
    std::string m_side;
    Ptr<NetDevice> m_dev;
};

// -----------------------------------------------------------------------------
// 1 Hz stats dump (как в two_channel.cc).
// -----------------------------------------------------------------------------
static void
emit_stats() {
    {
        std::ostringstream o;
        o << "{\"event_type\":\"network\",\"flow_id\":\"lora_uav_tx\","
          << "\"sim_time\":" << Simulator::Now().GetSeconds()
          << ",\"packets_tx\":" << g_uav.packets_sent
          << ",\"packets_rx\":" << g_gcs.packets_received
          << ",\"bytes_tx\":" << g_uav.bytes_sent
          << ",\"bytes_rx\":" << g_gcs.bytes_received
          << ",\"packets_dropped_interference\":0"
          << ",\"run_id\":\"" << g_run_id << "\"}";
        emit_event(o.str());
    }
    {
        std::ostringstream o;
        o << "{\"event_type\":\"network\",\"flow_id\":\"lora_gcs_tx\","
          << "\"sim_time\":" << Simulator::Now().GetSeconds()
          << ",\"packets_tx\":" << g_gcs.packets_sent
          << ",\"packets_rx\":" << g_uav.packets_received
          << ",\"bytes_tx\":" << g_gcs.bytes_sent
          << ",\"bytes_rx\":" << g_uav.bytes_received
          << ",\"packets_dropped_interference\":0"
          << ",\"run_id\":\"" << g_run_id << "\"}";
        emit_event(o.str());
    }
    Simulator::Schedule(Seconds(1.0), &emit_stats);
}

// -----------------------------------------------------------------------------
// main
// -----------------------------------------------------------------------------
int
main(int argc, char* argv[]) {
    // RealtimeSimulator — обязателен для PTY bridge.
    GlobalValue::Bind("SimulatorImplementationType",
                      StringValue("ns3::RealtimeSimulatorImpl"));

    std::string runId        = "lora_serial_smoke";
    std::string logDir       = "/work/logs";
    double duration_s        = 20.0;
    uint32_t sf              = 7;
    double bandwidth_hz      = 125000.0;
    double tx_power_dbm      = 14.0;
    double distance_m        = 1000.0;

    CommandLine cmd(__FILE__);
    cmd.AddValue("runId",       "ID прогона",                runId);
    cmd.AddValue("logDir",      "директория логов",          logDir);
    cmd.AddValue("duration",    "длительность, сек",         duration_s);
    cmd.AddValue("sf",          "Spreading Factor (7-12)",   sf);
    cmd.AddValue("bandwidth",   "Bandwidth Hz",              bandwidth_hz);
    cmd.AddValue("txPower",     "TX power dBm (info-only)",  tx_power_dbm);
    cmd.AddValue("distance",    "UAV-GCS distance, м",       distance_m);
    cmd.AddValue("ptyUavPath",  "path для UAV PTY",          g_pty_uav_path);
    cmd.AddValue("ptyGcsPath",  "path для GCS PTY",          g_pty_gcs_path);
    cmd.Parse(argc, argv);

    g_run_id = runId;

    std::string log_path = logDir + "/" + runId + "/ns3_events.jsonl";
    g_jsonl.open(log_path, std::ios::out | std::ios::app);
    if (!g_jsonl.is_open()) {
        std::cerr << "Cannot open " << log_path << "\n";
        return 1;
    }

    LoraPhyParams phy = calibrate_sx1276(sf, bandwidth_hz, 5.0);
    double per = per_for_distance(distance_m, sf);
    NS_LOG_UNCOND("[lora_serial] " << phy.sx1276_table_entry
                  << ", distance=" << distance_m << "m, PER=" << per);

    {
        std::ostringstream o;
        o << "{\"event_type\":\"component\",\"component\":\"ns3\",\"phase\":\"start\","
          << "\"scenario\":\"lora_serial\","
          << "\"run_id\":\"" << runId << "\","
          << "\"sf\":" << sf << ",\"bandwidth_hz\":" << bandwidth_hz
          << ",\"tx_power_dbm\":" << tx_power_dbm
          << ",\"distance_m\":" << distance_m
          << ",\"data_rate_bps\":" << static_cast<uint64_t>(phy.data_rate_bps)
          << ",\"airtime_ms\":" << phy.airtime_ms
          << ",\"per\":" << per << "}";
        emit_event(o.str());
    }

    // ---- PointToPoint channel калиброванный под SX1276 ----
    PointToPointHelper p2p;
    p2p.SetDeviceAttribute("DataRate",
        DataRateValue(DataRate(static_cast<uint64_t>(phy.data_rate_bps))));
    p2p.SetChannelAttribute("Delay",
        TimeValue(MilliSeconds(static_cast<int>(phy.airtime_ms))));

    NodeContainer nodes;
    nodes.Create(2);   // [0]=UAV, [1]=GCS
    NetDeviceContainer devs = p2p.Install(nodes);

    // ---- Per-packet error model калиброван под PER(distance) ----
    Ptr<RateErrorModel> em_uav = CreateObject<RateErrorModel>();
    em_uav->SetUnit(RateErrorModel::ERROR_UNIT_PACKET);
    em_uav->SetRate(per);
    Ptr<RateErrorModel> em_gcs = CreateObject<RateErrorModel>();
    em_gcs->SetUnit(RateErrorModel::ERROR_UNIT_PACKET);
    em_gcs->SetRate(per);
    devs.Get(0)->SetAttribute("ReceiveErrorModel", PointerValue(em_uav));
    devs.Get(1)->SetAttribute("ReceiveErrorModel", PointerValue(em_gcs));

    // ---- PtyApps ----
    if (g_pty_uav_path.empty() || g_pty_gcs_path.empty()) {
        std::cerr << "lora_serial: --ptyUavPath и --ptyGcsPath обязательны для 1.7.h.\n";
        return 1;
    }

    Ptr<PtyApp> uav_app = CreateObject<PtyApp>();
    uav_app->Configure(g_pty_uav_path, "uav", devs.Get(0));
    nodes.Get(0)->AddApplication(uav_app);
    uav_app->SetStartTime(Seconds(1.5));
    uav_app->SetStopTime(Seconds(duration_s - 0.5));

    Ptr<PtyApp> gcs_app = CreateObject<PtyApp>();
    gcs_app->Configure(g_pty_gcs_path, "gcs", devs.Get(1));
    nodes.Get(1)->AddApplication(gcs_app);
    gcs_app->SetStartTime(Seconds(1.5));
    gcs_app->SetStopTime(Seconds(duration_s - 0.5));

    devs.Get(0)->SetReceiveCallback(MakeCallback(&PtyApp::OnReceive, uav_app));
    devs.Get(1)->SetReceiveCallback(MakeCallback(&PtyApp::OnReceive, gcs_app));

    NS_LOG_UNCOND("[pty] UAV " << g_pty_uav_path
                  << " ↔ LoRa (full-duplex) ↔ GCS " << g_pty_gcs_path);

    Simulator::Schedule(Seconds(1.0), &emit_stats);
    Simulator::Stop(Seconds(duration_s));
    NS_LOG_UNCOND("[lora_serial] starting (duration=" << duration_s
                  << "s, " << phy.sx1276_table_entry << ", PER=" << per << ")");
    Simulator::Run();
    NS_LOG_UNCOND("[lora_serial] finished");

    emit_stats();
    {
        std::ostringstream o;
        o << "{\"event_type\":\"component\",\"component\":\"ns3\",\"phase\":\"stop\","
          << "\"run_id\":\"" << runId << "\"}";
        emit_event(o.str());
    }
    g_jsonl.close();
    Simulator::Destroy();
    return 0;
}
