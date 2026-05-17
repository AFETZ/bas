// Этап 1.7.b -- LoRa Serial канал через ns-3 lorawan module.
//
// Архитектура: 2 LoRa endpoints (UAV = EndDevice, GCS = Gateway)
// соединены через LoraChannel с реальной физикой ITU-R RP.452 propagation
// loss + LoraPhy + LorawanMac. Параметры SF/BW/CR настраиваются через
// CommandLine (по умолчанию SF=7, BW=125 kHz, как 1.7 acceptance profile).
//
// 1.7.b -- minimal scaffold БЕЗ PTY bridge: OneShotSenderHelper отправляет
// 1 пакет от UAV к GCS. Цель: проверить что lorawan module работает в
// нашем контексте, получить air time / received power / SNR метрики в
// JSONL.
//
// 1.7.c (next) -- добавит PtyTap helper который связывает /tmp/ptyUAV_lora
// и /tmp/ptyGCS_lora с LoRa MAC так чтобы MAVLink-байты текли через
// ns-3 lorawan.

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/mobility-helper.h"
#include "ns3/lorawan-module.h"
#include "ns3/applications-module.h"

// POSIX для PTY (1.7.c).
#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>     // posix_openpt, grantpt, unlockpt, ptsname
#include <sys/stat.h>   // symlink

#include <cerrno>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>

using namespace ns3;
using namespace lorawan;

NS_LOG_COMPONENT_DEFINE("LoraSerialScenario");

// -----------------------------------------------------------------------------
// JSONL log (как в two_channel.cc).
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
    uint64_t packets_lost_interference = 0;
    uint64_t bytes_sent = 0;
    uint64_t bytes_received = 0;
};

static SideStats g_uav;
static SideStats g_gcs;

// Trace callbacks для LoraPhy.
// Signature: `TracedCallback<Ptr<const Packet>, uint32_t>` -- второй аргумент
// это `nodeId`. См. /tmp/lorawan/model/lora-phy.h:301,317,329.
static void
on_phy_send(std::string side, Ptr<const Packet> p, uint32_t nodeId) {
    SideStats& s = (side == "uav") ? g_uav : g_gcs;
    s.packets_sent += 1;
    s.bytes_sent += p->GetSize();

    std::ostringstream o;
    o << "{\"event_type\":\"component\",\"component\":\"ns3:lorawan\","
      << "\"phase\":\"phy_send\",\"side\":\"" << side << "\","
      << "\"node_id\":" << nodeId << ","
      << "\"sim_time\":" << Simulator::Now().GetSeconds() << ","
      << "\"bytes\":" << p->GetSize() << ","
      << "\"run_id\":\"" << g_run_id << "\"}";
    emit_event(o.str());
}

static void
on_phy_received(std::string side, Ptr<const Packet> p, uint32_t nodeId) {
    SideStats& s = (side == "uav") ? g_uav : g_gcs;
    s.packets_received += 1;
    s.bytes_received += p->GetSize();

    std::ostringstream o;
    o << "{\"event_type\":\"component\",\"component\":\"ns3:lorawan\","
      << "\"phase\":\"phy_received\",\"side\":\"" << side << "\","
      << "\"node_id\":" << nodeId << ","
      << "\"sim_time\":" << Simulator::Now().GetSeconds() << ","
      << "\"bytes\":" << p->GetSize() << ","
      << "\"run_id\":\"" << g_run_id << "\"}";
    emit_event(o.str());
}

static void
on_interfered(std::string side, Ptr<const Packet> p, uint32_t nodeId) {
    SideStats& s = (side == "uav") ? g_uav : g_gcs;
    s.packets_lost_interference += 1;

    std::ostringstream o;
    o << "{\"event_type\":\"component\",\"component\":\"ns3:lorawan\","
      << "\"phase\":\"phy_lost_interference\",\"side\":\"" << side << "\","
      << "\"node_id\":" << nodeId << ","
      << "\"sim_time\":" << Simulator::Now().GetSeconds() << ","
      << "\"bytes\":" << (p ? p->GetSize() : 0) << ","
      << "\"run_id\":\"" << g_run_id << "\"}";
    emit_event(o.str());
}

// -----------------------------------------------------------------------------
// 1.7.c -- PtyApp: ns-3 Application которая bridges PTY <-> LoRa MAC.
//
// АРХИТЕКТУРА (community-validated pattern, см. ArduPilot Discourse
// "Simulating a serial device with SITL" + mavlink-router examples/config.sample):
//
//   HOST:
//     socat PTY,link=/tmp/ptyUAV_lora UNIX-LISTEN:/tmp/bas-bridge/lora.sock
//        ^                                ^
//        |                                |-- mavlink-router/orchestrator
//        |                                |   подключаются ЗДЕСЬ
//        ^-- внешний host-side PTY
//
//   CONTAINER (bind-mount /tmp/bas-bridge:/bridge):
//     socat PTY,link=/work/pty/ptyUAV_lora UNIX-CONNECT:/bridge/lora.sock
//        ^
//        |-- container-side PTY, ns-3 PtyApp подключается ЗДЕСЬ
//
// PtyApp САМ НЕ СОЗДАЁТ PTY (это делает socat). Application просто
// `open(path)` -- посимвольно читает байты и инжектит в LoRa MAC.
//
// Это решает проблему pts namespace docker (validated в docker/for-linux#77).
//
// UAV-side: каждые 10 мс читает байты с m_master_fd, инжектит как пакет
// в lora MAC через dev->Send().
// GCS-side: получает Packet от phy trace, пишет байты обратно в m_master_fd.
// -----------------------------------------------------------------------------
static std::string g_pty_uav_path = "";
static std::string g_pty_gcs_path = "";

class PtyApp : public Application {
public:
    static TypeId GetTypeId() {
        static TypeId tid = TypeId("PtyApp")
            .SetParent<Application>()
            .AddConstructor<PtyApp>();
        return tid;
    }

    PtyApp() : m_master_fd(-1) {}
    ~PtyApp() override {
        if (m_master_fd >= 0) close(m_master_fd);
    }

    /// `pty_path` -- container-side PTY (созданный socat'ом ДО запуска
    /// ns-3), который PtyApp просто open'ит. Например `/work/pty/ptyUAV_lora`.
    void Configure(const std::string& pty_path,
                   const std::string& side,
                   Ptr<LoraNetDevice> dev) {
        m_pty_path = pty_path;
        m_side = side;
        m_dev = dev;
    }

    // GCS-side callback от LoraPhy::ReceivedPacket -- пишем bytes обратно в PTY.
    void OnGwReceive(Ptr<const Packet> p, uint32_t /*nodeId*/) {
        if (m_master_fd < 0) return;
        std::vector<uint8_t> buf(p->GetSize());
        p->CopyData(buf.data(), buf.size());
        ssize_t w = write(m_master_fd, buf.data(), buf.size());
        std::ostringstream o;
        o << "{\"event_type\":\"component\",\"component\":\"ns3:lorawan\","
          << "\"phase\":\"pty_write\",\"side\":\"" << m_side << "\","
          << "\"sim_time\":" << Simulator::Now().GetSeconds() << ","
          << "\"bytes\":" << w << ",\"run_id\":\"" << g_run_id << "\"}";
        emit_event(o.str());
    }

private:
    void StartApplication() override {
        // Открываем уже существующий PTY (создан socat'ом перед запуском
        // ns-3). Это socat'овский slave-side PTY: чтение/запись идут на
        // байт-стрим, который socat зеркалит через UNIX socket на host.
        // O_NOCTTY -- не делать этот PTY controlling tty процесса.
        // O_NONBLOCK -- read() возвращает -1/EAGAIN если данных нет.
        m_master_fd = open(m_pty_path.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
        if (m_master_fd < 0) {
            NS_LOG_UNCOND("[pty] open(" << m_pty_path << ") failed: "
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

        // UAV-side: запускаем polling loop -- читаем байты из PTY и отдаём в LoRa.
        if (m_side == "uav") {
            Simulator::Schedule(MilliSeconds(10), &PtyApp::PollAndSend, this);
        }
    }

    void StopApplication() override {
        if (m_master_fd >= 0) {
            close(m_master_fd);
            m_master_fd = -1;
        }
        // НЕ unlink m_pty_path -- его создаёт socat, не мы.
    }

    void PollAndSend() {
        if (m_master_fd < 0) return;
        uint8_t buf[256];   // LoRa MAC frame size limit
        ssize_t n = read(m_master_fd, buf, sizeof(buf));
        if (n > 0) {
            Ptr<Packet> packet = Create<Packet>(buf, n);
            if (m_dev) {
                // LoraNetDevice::Send(packet, dst, protocolNumber)
                // dst = "ff" broadcast, protocol = 0 ignored.
                m_dev->Send(packet, Address(), 0);
            }
            std::ostringstream o;
            o << "{\"event_type\":\"component\","
              << "\"component\":\"ns3:lorawan\","
              << "\"phase\":\"pty_read\",\"side\":\"" << m_side << "\","
              << "\"sim_time\":" << Simulator::Now().GetSeconds() << ","
              << "\"bytes\":" << n << ",\"run_id\":\"" << g_run_id << "\"}";
            emit_event(o.str());
        }
        // Перезапланируем polling.
        Simulator::Schedule(MilliSeconds(10), &PtyApp::PollAndSend, this);
    }

    int m_master_fd;
    std::string m_pty_path;
    std::string m_side;
    Ptr<LoraNetDevice> m_dev;
};

// -----------------------------------------------------------------------------
// Периодический stats dump (1 Hz).
// -----------------------------------------------------------------------------
static void
emit_stats() {
    std::ostringstream o;
    o << "{\"event_type\":\"network\",\"flow_id\":\"lora_uav_tx\","
      << "\"sim_time\":" << Simulator::Now().GetSeconds() << ","
      << "\"packets_tx\":" << g_uav.packets_sent << ","
      << "\"packets_rx\":" << g_gcs.packets_received << ","
      << "\"bytes_tx\":" << g_uav.bytes_sent << ","
      << "\"bytes_rx\":" << g_gcs.bytes_received << ","
      << "\"packets_dropped_interference\":" << g_gcs.packets_lost_interference
      << ",\"run_id\":\"" << g_run_id << "\"}";
    emit_event(o.str());

    Simulator::Schedule(Seconds(1.0), &emit_stats);
}

// -----------------------------------------------------------------------------
// main
// -----------------------------------------------------------------------------
int
main(int argc, char* argv[]) {
    // Realtime simulator — необходим для PTY bridge (1.7.c): без него
    // 20с симуляции проходит за <1 сек wall-clock и Poll cycle ns-3 PtyApp
    // не успевает прочитать host-side байты.
    GlobalValue::Bind("SimulatorImplementationType",
                      StringValue("ns3::RealtimeSimulatorImpl"));

    std::string runId        = "lora_serial_smoke";
    std::string logDir       = "/work/logs";
    double duration_s        = 20.0;
    uint32_t sf              = 7;
    double bandwidth_hz      = 125000.0;
    double tx_power_dbm      = 14.0;
    double distance_m        = 1000.0;  // UAV-GCS distance

    CommandLine cmd(__FILE__);
    cmd.AddValue("runId",       "ID прогона",                runId);
    cmd.AddValue("logDir",      "директория логов",          logDir);
    cmd.AddValue("duration",    "длительность, сек",         duration_s);
    cmd.AddValue("sf",          "Spreading Factor (7-12)",   sf);
    cmd.AddValue("bandwidth",   "Bandwidth Hz",              bandwidth_hz);
    cmd.AddValue("txPower",     "TX power dBm",              tx_power_dbm);
    cmd.AddValue("distance",    "UAV-GCS distance, м",       distance_m);
    cmd.AddValue("ptyUavPath",  "symlink path для UAV PTY (1.7.c)",  g_pty_uav_path);
    cmd.AddValue("ptyGcsPath",  "symlink path для GCS PTY (1.7.c)",  g_pty_gcs_path);
    cmd.Parse(argc, argv);

    g_run_id = runId;

    std::string log_path = logDir + "/" + runId + "/ns3_events.jsonl";
    g_jsonl.open(log_path, std::ios::out | std::ios::app);
    if (!g_jsonl.is_open()) {
        std::cerr << "Cannot open " << log_path << "\n";
        return 1;
    }

    // Стартовое событие.
    {
        std::ostringstream o;
        o << "{\"event_type\":\"component\",\"component\":\"ns3\",\"phase\":\"start\","
          << "\"scenario\":\"lora_serial\","
          << "\"run_id\":\"" << runId << "\","
          << "\"sf\":" << sf << ",\"bandwidth_hz\":" << bandwidth_hz
          << ",\"tx_power_dbm\":" << tx_power_dbm
          << ",\"distance_m\":" << distance_m << "}";
        emit_event(o.str());
    }

    // ---- LoRa channel ----
    Ptr<LogDistancePropagationLossModel> loss =
        CreateObject<LogDistancePropagationLossModel>();
    loss->SetPathLossExponent(3.76);
    loss->SetReference(1, 7.7);
    Ptr<PropagationDelayModel> delay =
        CreateObject<ConstantSpeedPropagationDelayModel>();
    Ptr<LoraChannel> channel = CreateObject<LoraChannel>(loss, delay);

    // ---- Mobility ----
    MobilityHelper mobility;
    Ptr<ListPositionAllocator> alloc = CreateObject<ListPositionAllocator>();
    alloc->Add(Vector(0, 0, 0));            // UAV (EndDevice)
    alloc->Add(Vector(distance_m, 0, 0));   // GCS (Gateway)
    mobility.SetPositionAllocator(alloc);
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");

    // ---- Helpers ----
    LoraPhyHelper phyHelper;
    phyHelper.SetChannel(channel);
    LorawanMacHelper macHelper;
    LoraHelper helper;

    // ---- UAV = EndDevice ----
    NodeContainer uav;
    uav.Create(1);
    mobility.Install(uav);
    phyHelper.SetDeviceType(LoraPhyHelper::ED);
    macHelper.SetDeviceType(LorawanMacHelper::ED_A);
    NetDeviceContainer uav_nd = helper.Install(phyHelper, macHelper, uav);

    // ---- GCS = Gateway ----
    NodeContainer gcs;
    gcs.Create(1);
    mobility.Install(gcs);
    phyHelper.SetDeviceType(LoraPhyHelper::GW);
    macHelper.SetDeviceType(LorawanMacHelper::GW);
    NetDeviceContainer gcs_nd = helper.Install(phyHelper, macHelper, gcs);

    // SF setup для UAV (статически, без ADR).
    macHelper.SetSpreadingFactorsUp(uav, gcs, channel);

    // ---- Trace ----
    Ptr<LoraNetDevice> uav_lora =
        DynamicCast<LoraNetDevice>(uav_nd.Get(0));
    Ptr<LoraNetDevice> gcs_lora =
        DynamicCast<LoraNetDevice>(gcs_nd.Get(0));
    if (uav_lora) {
        uav_lora->GetPhy()->TraceConnectWithoutContext(
            "StartSending",
            MakeBoundCallback(&on_phy_send, "uav"));
    }
    if (gcs_lora) {
        gcs_lora->GetPhy()->TraceConnectWithoutContext(
            "ReceivedPacket",
            MakeBoundCallback(&on_phy_received, "gcs"));
        gcs_lora->GetPhy()->TraceConnectWithoutContext(
            "LostPacketBecauseInterference",
            MakeBoundCallback(&on_interfered, "gcs"));
    }

    // ---- Sender application: один пакет каждую секунду от UAV ----
    // 1.7.b smoke path -- если PTY не запрошен, шлём synthetic трафик.
    // В 1.7.c при `--ptyUavPath/--ptyGcsPath` ставим PtyApp вместо.
    bool use_pty = !g_pty_uav_path.empty() && !g_pty_gcs_path.empty();
    Ptr<PtyApp> uav_app, gcs_app;
    if (use_pty) {
        // UAV-side PTY: orchestrator/mavlink-router пишет MAVLink байты сюда,
        // PtyApp читает и инжектит как LoRa MAC payload вверх по каналу.
        uav_app = CreateObject<PtyApp>();
        uav_app->Configure(g_pty_uav_path, "uav",
                           DynamicCast<LoraNetDevice>(uav_nd.Get(0)));
        uav.Get(0)->AddApplication(uav_app);
        uav_app->SetStartTime(Seconds(1.5));
        uav_app->SetStopTime(Seconds(duration_s - 0.5));

        // GCS-side: PtyApp принимает байты из LoRa Phy через trace и пишет
        // в свой PTY (orchestrator читает оттуда).
        gcs_app = CreateObject<PtyApp>();
        gcs_app->Configure(g_pty_gcs_path, "gcs",
                           DynamicCast<LoraNetDevice>(gcs_nd.Get(0)));
        gcs.Get(0)->AddApplication(gcs_app);
        gcs_app->SetStartTime(Seconds(1.5));
        gcs_app->SetStopTime(Seconds(duration_s - 0.5));

        // Привязываем gcs PHY trace ReceivedPacket к gcs_app->ForwardToPty
        if (gcs_lora) {
            gcs_lora->GetPhy()->TraceConnectWithoutContext(
                "ReceivedPacket",
                MakeCallback(&PtyApp::OnGwReceive, gcs_app));
        }
        NS_LOG_UNCOND("[pty] UAV " << g_pty_uav_path
                      << " <-> LoRa <-> GCS " << g_pty_gcs_path);
    } else {
        PeriodicSenderHelper sender;
        sender.SetPeriod(Seconds(1.0));
        sender.SetPacketSize(50);   // ~MAVLink heartbeat
        ApplicationContainer sender_apps = sender.Install(uav);
        sender_apps.Start(Seconds(2.0));
        sender_apps.Stop(Seconds(duration_s - 1.0));
    }

    // ---- Stats dump ----
    Simulator::Schedule(Seconds(1.0), &emit_stats);

    Simulator::Stop(Seconds(duration_s));
    NS_LOG_UNCOND("[lora_serial] starting (duration=" << duration_s
                  << "s, sf=" << sf << ", bw=" << bandwidth_hz << " Hz, "
                  << "distance=" << distance_m << " m)");
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
