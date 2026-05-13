// two_channel.cc - ns-3 сценарий радио-петли для первого прототипа БАС.
//
// Топология (на канал control / payload — независимо):
//
//     tap-<chan>-near  <--TapBridge-->  ns-3 node 0 <==CSMA link==> ns-3 node 1  <--TapBridge-->  tap-<chan>-far
//                                       (no IP, L2)                  (no IP, L2)
//
// На CSMA-линке навешан RateErrorModel (packet_loss_ratio) и DelayModel (delay_ms,
// эмулируется как constant-channel-delay). Outage реализован через шедулинг:
// в окне outage error rate выкручивается в 1.0 (все пакеты теряются), после окна
// возвращается к базовой норме.
//
// Параметры профиля передаются через CommandLine. JSONL события (per-flow stats
// раз в секунду + outage edges) пишутся в /work/logs/<runId>/ns3_events.jsonl.

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/csma-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/tap-bridge-module.h"

#include <fstream>
#include <iomanip>
#include <map>
#include <sstream>
#include <string>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("BasTwoChannel");

// -----------------------------------------------------------------------------
// Параметры одного канала.
// -----------------------------------------------------------------------------
struct ChannelParams {
    std::string name;             // "control" / "payload"
    std::string tap_near;         // "tap-ctrl-near"
    std::string tap_far;          // "tap-ctrl-far"
    double bandwidth_mbps;
    double delay_ms;
    double packet_loss_ratio;     // базовая
    std::vector<std::pair<double, double>> outage_periods;  // (start_s, end_s)
};

// Статистика по каналу (счётчики обновляются по trace-source'ам).
struct ChannelStats {
    uint64_t bytes_tx = 0;
    uint64_t bytes_rx = 0;
    uint64_t packets_tx = 0;
    uint64_t packets_rx = 0;
    uint64_t packets_dropped_phy = 0;
    bool in_outage = false;
};

// Глобальный лог-файл (открывается в main, закрывается при выходе).
static std::ofstream g_jsonl;

static void
emit_event(const std::string& json_obj) {
    if (g_jsonl.is_open()) {
        g_jsonl << json_obj << "\n";
        g_jsonl.flush();
    }
}

static std::string
escape_json(const std::string& s) {
    std::string out;
    for (char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            default: out += c;
        }
    }
    return out;
}

// -----------------------------------------------------------------------------
// Trace callbacks.
// -----------------------------------------------------------------------------
static std::map<std::string, ChannelStats> g_stats;
static std::map<std::string, Ptr<RateErrorModel>> g_error_models;
static std::map<std::string, double> g_base_loss;

static void
on_csma_tx(std::string ch, Ptr<const Packet> p) {
    auto& s = g_stats[ch];
    s.bytes_tx += p->GetSize();
    s.packets_tx += 1;
}

static void
on_csma_rx(std::string ch, Ptr<const Packet> p) {
    auto& s = g_stats[ch];
    s.bytes_rx += p->GetSize();
    s.packets_rx += 1;
}

static void
on_csma_phy_drop(std::string ch, Ptr<const Packet>) {
    g_stats[ch].packets_dropped_phy += 1;
}

// -----------------------------------------------------------------------------
// Outage schedule (set error rate to 1.0 during outage windows).
// -----------------------------------------------------------------------------
static void
outage_begin(std::string ch) {
    auto em = g_error_models[ch];
    if (em) {
        em->SetAttribute("ErrorRate", DoubleValue(1.0));
    }
    g_stats[ch].in_outage = true;

    std::ostringstream o;
    o << "{\"event_type\":\"component\",\"component\":\"ns3:" << ch
      << "\",\"phase\":\"outage_begin\",\"sim_time\":" << Simulator::Now().GetSeconds() << "}";
    emit_event(o.str());
}

static void
outage_end(std::string ch) {
    auto em = g_error_models[ch];
    if (em) {
        em->SetAttribute("ErrorRate", DoubleValue(g_base_loss[ch]));
    }
    g_stats[ch].in_outage = false;

    std::ostringstream o;
    o << "{\"event_type\":\"component\",\"component\":\"ns3:" << ch
      << "\",\"phase\":\"outage_end\",\"sim_time\":" << Simulator::Now().GetSeconds() << "}";
    emit_event(o.str());
}

// -----------------------------------------------------------------------------
// Периодический emit статистики канала (1 Hz).
// -----------------------------------------------------------------------------
static void
emit_stats(std::string runId) {
    double t = Simulator::Now().GetSeconds();
    for (const auto& [name, s] : g_stats) {
        std::ostringstream o;
        o << "{\"event_type\":\"network\""
          << ",\"run_id\":\"" << runId << "\""
          << ",\"sim_time\":" << t
          << ",\"flow_id\":\"" << name << "\""
          << ",\"bytes_tx\":" << s.bytes_tx
          << ",\"bytes_rx\":" << s.bytes_rx
          << ",\"packets_tx\":" << s.packets_tx
          << ",\"packets_rx\":" << s.packets_rx
          << ",\"packets_dropped_phy\":" << s.packets_dropped_phy
          << ",\"outage_state\":" << (s.in_outage ? "true" : "false")
          << "}";
        emit_event(o.str());
    }
    Simulator::Schedule(Seconds(1.0), &emit_stats, runId);
}

// -----------------------------------------------------------------------------
// Создание одного канала: 2 ноды + CSMA + 2 TapBridge UseLocal.
// -----------------------------------------------------------------------------
static void
build_channel(const ChannelParams& p) {
    NS_LOG_UNCOND("[channel " << p.name << "] tap_near=" << p.tap_near
                  << " tap_far=" << p.tap_far
                  << " bw=" << p.bandwidth_mbps << "Mbps"
                  << " delay=" << p.delay_ms << "ms"
                  << " loss=" << p.packet_loss_ratio);

    // 2 ноды для near и far endpoints.
    NodeContainer nodes;
    nodes.Create(2);

    // CSMA как simulated wire с rate + delay. TapBridge требует CSMA-совместимый
    // NetDevice (P2P не поддерживается).
    CsmaHelper csma;
    std::ostringstream rate_ss;
    rate_ss << static_cast<uint64_t>(p.bandwidth_mbps * 1'000'000) << "bps";
    csma.SetChannelAttribute("DataRate", StringValue(rate_ss.str()));
    csma.SetChannelAttribute("Delay", TimeValue(MilliSeconds(p.delay_ms)));
    // Большой DropTail queue — BDP для 20Mbps × 500ms RTT ~800 пакетов.
    // Default 100p выкидывает ARP когда Gazebo Transport multicast наполняет канал.
    csma.SetQueue("ns3::DropTailQueue", "MaxSize", StringValue("5000p"));

    NetDeviceContainer devs = csma.Install(nodes);

    // Внутренний адресный план NS3 (никуда не уходит — TapBridge UseLocal
    // делает L2-мост, IP-адреса присваиваем только для ARP в test'е).
    // Не назначаем — TapBridge берёт MAC и L2 проходит "прозрачно".

    // Error model на обеих сторонах (симметричная потеря).
    Ptr<RateErrorModel> em = CreateObject<RateErrorModel>();
    em->SetAttribute("ErrorRate", DoubleValue(p.packet_loss_ratio));
    em->SetAttribute("ErrorUnit", StringValue("ERROR_UNIT_PACKET"));
    devs.Get(0)->SetAttribute("ReceiveErrorModel", PointerValue(em));
    devs.Get(1)->SetAttribute("ReceiveErrorModel", PointerValue(em));
    g_error_models[p.name] = em;
    g_base_loss[p.name] = p.packet_loss_ratio;

    // TapBridge для каждой стороны: подключаем к существующим TAP'ам на host'е.
    TapBridgeHelper tap_helper;
    tap_helper.SetAttribute("Mode", StringValue("UseLocal"));

    tap_helper.SetAttribute("DeviceName", StringValue(p.tap_near));
    tap_helper.Install(nodes.Get(0), devs.Get(0));

    tap_helper.SetAttribute("DeviceName", StringValue(p.tap_far));
    tap_helper.Install(nodes.Get(1), devs.Get(1));

    // Trace TX/RX.
    devs.Get(0)->TraceConnectWithoutContext(
        "PhyTxEnd", MakeBoundCallback(&on_csma_tx, p.name));
    devs.Get(1)->TraceConnectWithoutContext(
        "PhyRxEnd", MakeBoundCallback(&on_csma_rx, p.name));
    devs.Get(1)->TraceConnectWithoutContext(
        "PhyTxEnd", MakeBoundCallback(&on_csma_tx, p.name));
    devs.Get(0)->TraceConnectWithoutContext(
        "PhyRxEnd", MakeBoundCallback(&on_csma_rx, p.name));
    devs.Get(0)->TraceConnectWithoutContext(
        "PhyRxDrop", MakeBoundCallback(&on_csma_phy_drop, p.name));
    devs.Get(1)->TraceConnectWithoutContext(
        "PhyRxDrop", MakeBoundCallback(&on_csma_phy_drop, p.name));

    g_stats[p.name] = {};

    // Outage расписание.
    for (const auto& [start, end] : p.outage_periods) {
        Simulator::Schedule(Seconds(start), &outage_begin, p.name);
        Simulator::Schedule(Seconds(end),   &outage_end,   p.name);
    }
}

// -----------------------------------------------------------------------------
// Парсинг outage_periods из CLI: формат "10-13,25-27".
// -----------------------------------------------------------------------------
static std::vector<std::pair<double, double>>
parse_outage(const std::string& s) {
    std::vector<std::pair<double, double>> out;
    if (s.empty()) return out;
    std::stringstream ss(s);
    std::string item;
    while (std::getline(ss, item, ',')) {
        auto dash = item.find('-');
        if (dash == std::string::npos) continue;
        double a = std::stod(item.substr(0, dash));
        double b = std::stod(item.substr(dash + 1));
        out.emplace_back(a, b);
    }
    return out;
}

// =============================================================================
int main(int argc, char* argv[]) {
    // Realtime scheduler — для синхронизации с реальным временем хоста (и SITL).
    GlobalValue::Bind("SimulatorImplementationType", StringValue("ns3::RealtimeSimulatorImpl"));
    GlobalValue::Bind("ChecksumEnabled", BooleanValue(true));

    std::string runId        = "dev";
    std::string logDir       = "/work/logs";
    double duration_s        = 300.0;

    // control channel
    double ctrl_bw_mbps      = 20.0;
    double ctrl_delay_ms     = 5.0;
    double ctrl_loss         = 0.0;
    std::string ctrl_outage  = "";

    // payload channel
    double pload_bw_mbps     = 20.0;
    double pload_delay_ms    = 5.0;
    double pload_loss        = 0.0;
    std::string pload_outage = "";

    CommandLine cmd(__FILE__);
    cmd.AddValue("runId",         "ID прогона из оркестратора",            runId);
    cmd.AddValue("logDir",        "директория логов (с слешем)",           logDir);
    cmd.AddValue("duration",      "длительность симуляции, секунд",        duration_s);

    cmd.AddValue("ctrlBandwidthMbps", "control канал: пропускная, Мбит/с", ctrl_bw_mbps);
    cmd.AddValue("ctrlDelayMs",       "control канал: задержка, мс",       ctrl_delay_ms);
    cmd.AddValue("ctrlLoss",          "control канал: доля потерь [0..1]", ctrl_loss);
    cmd.AddValue("ctrlOutage",        "control канал: окна разрыва, \"a-b,c-d\"", ctrl_outage);

    cmd.AddValue("ploadBandwidthMbps","payload канал: пропускная, Мбит/с", pload_bw_mbps);
    cmd.AddValue("ploadDelayMs",      "payload канал: задержка, мс",       pload_delay_ms);
    cmd.AddValue("ploadLoss",         "payload канал: доля потерь [0..1]", pload_loss);
    cmd.AddValue("ploadOutage",       "payload канал: окна разрыва, \"a-b,c-d\"", pload_outage);

    cmd.Parse(argc, argv);

    // Открыть JSONL-журнал.
    std::string log_path = logDir + "/" + runId + "/ns3_events.jsonl";
    g_jsonl.open(log_path, std::ios::out | std::ios::app);
    if (!g_jsonl.is_open()) {
        std::cerr << "Cannot open " << log_path << "\n";
        return 1;
    }

    // Стартовое событие.
    {
        std::ostringstream o;
        o << "{\"event_type\":\"component\",\"component\":\"ns3\",\"phase\":\"start\""
          << ",\"run_id\":\"" << runId << "\""
          << ",\"duration_s\":" << duration_s
          << ",\"ctrl\":{\"bw_mbps\":" << ctrl_bw_mbps
          <<   ",\"delay_ms\":" << ctrl_delay_ms
          <<   ",\"loss\":" << ctrl_loss
          <<   ",\"outage\":\"" << escape_json(ctrl_outage) << "\"}"
          << ",\"pload\":{\"bw_mbps\":" << pload_bw_mbps
          <<   ",\"delay_ms\":" << pload_delay_ms
          <<   ",\"loss\":" << pload_loss
          <<   ",\"outage\":\"" << escape_json(pload_outage) << "\"}"
          << "}";
        emit_event(o.str());
    }

    // Конфигурируем оба канала.
    build_channel({
        .name = "control",
        .tap_near = "tap-ctrl-near",
        .tap_far  = "tap-ctrl-far",
        .bandwidth_mbps = ctrl_bw_mbps,
        .delay_ms = ctrl_delay_ms,
        .packet_loss_ratio = ctrl_loss,
        .outage_periods = parse_outage(ctrl_outage),
    });

    build_channel({
        .name = "payload",
        .tap_near = "tap-pload-near",
        .tap_far  = "tap-pload-far",
        .bandwidth_mbps = pload_bw_mbps,
        .delay_ms = pload_delay_ms,
        .packet_loss_ratio = pload_loss,
        .outage_periods = parse_outage(pload_outage),
    });

    // Периодический emit stats (раз в секунду).
    Simulator::Schedule(Seconds(1.0), &emit_stats, runId);

    Simulator::Stop(Seconds(duration_s));
    NS_LOG_UNCOND("ns-3 starting (duration=" << duration_s << "s)");
    Simulator::Run();
    NS_LOG_UNCOND("ns-3 finished");

    // Финальный snapshot.
    emit_stats(runId);
    {
        std::ostringstream o;
        o << "{\"event_type\":\"component\",\"component\":\"ns3\",\"phase\":\"stop\""
          << ",\"run_id\":\"" << runId << "\"}";
        emit_event(o.str());
    }
    g_jsonl.close();

    Simulator::Destroy();
    return 0;
}
