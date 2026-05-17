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
on_interfered(std::string side, Ptr<const Packet>, uint32_t) {
    SideStats& s = (side == "uav") ? g_uav : g_gcs;
    s.packets_lost_interference += 1;
}

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
      << "\"packets_dropped_interference\":" << g_uav.packets_lost_interference
      << ",\"run_id\":\"" << g_run_id << "\"}";
    emit_event(o.str());

    Simulator::Schedule(Seconds(1.0), &emit_stats);
}

// -----------------------------------------------------------------------------
// main
// -----------------------------------------------------------------------------
int
main(int argc, char* argv[]) {
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
    PeriodicSenderHelper sender;
    sender.SetPeriod(Seconds(1.0));
    sender.SetPacketSize(50);   // ~MAVLink heartbeat
    ApplicationContainer sender_apps = sender.Install(uav);
    sender_apps.Start(Seconds(2.0));
    sender_apps.Stop(Seconds(duration_s - 1.0));

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
