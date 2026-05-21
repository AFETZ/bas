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

#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iterator>
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
static std::map<std::string, Ptr<CsmaChannel>> g_csma_channels;   // 2.1.d: для dynamic delay
static std::map<std::string, double> g_base_loss;
static std::map<std::string, double> g_base_delay_ms;             // 2.1.d: base delay из CLI

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
// Sionna RT dynamic loss update (этап 2.1.d).
// Читает /tmp/sionna_channel.json (или указанный путь), парсит "loss_ratio"
// (простой regex, не нужен JSON-парсер), и обновляет RateErrorModel для
// канала payload (приложение Sionna — к видео-каналу, control остаётся
// с outage-расписанием). Polling раз в 100 мс через Simulator::Schedule.
// -----------------------------------------------------------------------------
static std::string g_sionna_path = "";
// target_flow: какой канал деформирует Sionna live update.
//   "payload" — только видео (back-compat, было so до этого изменения)
//   "control" — только MAVLink/команды (для NLOS-демо без визуала)
//   "both"    — оба канала одновременно (реалистично: за зданием падает и
//                 видео, и управление, как в реальной физике радио)
// Применяется к loss_ratio и extra_delay_ms идентично.
static std::string g_sionna_target_flow = "payload";
static double g_sionna_last_loss = -1.0;       // для логов: эмитить event только при изменении
static double g_sionna_last_delay_ms = -1.0;   // 2.1.d: то же для delay

// Возвращает список каналов которым применяется Sionna update.
// Хелпер чтобы избежать ifelse-каскада в sionna_poll_tick.
static std::vector<std::string>
sionna_target_channels() {
    if (g_sionna_target_flow == "both") {
        return {"control", "payload"};
    }
    if (g_sionna_target_flow == "control") {
        return {"control"};
    }
    return {"payload"};   // default + "payload" + любое неизвестное значение
}

static double
parse_sionna_field(const std::string& body, const std::string& key_name) {
    // Файл маленький, ищем `"<key>":<float>` подстроку.
    std::string key = "\"" + key_name + "\":";
    auto pos = body.find(key);
    if (pos == std::string::npos) return -1.0;
    pos += key.size();
    char* endp = nullptr;
    double val = std::strtod(body.c_str() + pos, &endp);
    if (endp == body.c_str() + pos) return -1.0;
    return val;
}

static void
sionna_poll_tick(std::string runId) {
    if (!g_sionna_path.empty()) {
        std::ifstream f(g_sionna_path);
        if (f.is_open()) {
            std::string body((std::istreambuf_iterator<char>(f)),
                             std::istreambuf_iterator<char>());

            auto targets = sionna_target_channels();

            // ---- loss_ratio ----
            double loss = parse_sionna_field(body, "loss_ratio");
            bool loss_changed = false;
            if (loss >= 0.0 && loss <= 1.0) {
                for (const auto& ch : targets) {
                    auto it = g_error_models.find(ch);
                    if (it == g_error_models.end() || !it->second) continue;
                    // Не перетираем outage-блокировку: outage кладёт ErrorRate=1.0.
                    if (!g_stats[ch].in_outage) {
                        it->second->SetAttribute(
                            "ErrorRate", DoubleValue(loss));
                    }
                }
                if (std::abs(loss - g_sionna_last_loss) > 0.01) {
                    g_sionna_last_loss = loss;
                    loss_changed = true;
                }
            }

            // ---- extra_delay_ms (Sionna multi-path/scattering propagation delay) ----
            // Применяется как `base_delay + extra_delay` к каждому каналу из targets.
            // Note: base_delay у control и payload разный (5 ms vs 10 ms по дефолту),
            // поэтому в логи пишем delay по первому target — это для оператора
            // ориентировочный показатель; реальные значения в emit_stats.
            double extra_delay_ms = parse_sionna_field(body, "extra_delay_ms");
            bool delay_changed = false;
            double logged_total_delay_ms = 0.0;
            if (extra_delay_ms >= 0.0 && extra_delay_ms <= 5000.0) {
                for (const auto& ch : targets) {
                    auto ch_it = g_csma_channels.find(ch);
                    auto base_it = g_base_delay_ms.find(ch);
                    if (ch_it == g_csma_channels.end() || !ch_it->second) continue;
                    if (base_it == g_base_delay_ms.end()) continue;
                    double total_delay_ms = base_it->second + extra_delay_ms;
                    ch_it->second->SetAttribute(
                        "Delay", TimeValue(MilliSeconds(total_delay_ms)));
                    if (logged_total_delay_ms == 0.0) {
                        logged_total_delay_ms = total_delay_ms;
                    }
                }
                if (std::abs(extra_delay_ms - g_sionna_last_delay_ms) > 0.5) {
                    g_sionna_last_delay_ms = extra_delay_ms;
                    delay_changed = true;
                }
            }

            // Один JSONL event если хоть что-то изменилось.
            if (loss_changed || delay_changed) {
                // target_flow в логи как одна строка: "payload", "control",
                // "control+payload" (для both) — оператор сразу видит scope.
                std::string flow_label;
                for (size_t i = 0; i < targets.size(); ++i) {
                    if (i > 0) flow_label += "+";
                    flow_label += targets[i];
                }
                std::ostringstream o;
                o << "{\"event_type\":\"component\","
                  << "\"component\":\"ns3:sionna_poll\","
                  << "\"phase\":\"channel_updated\","
                  << "\"sim_time\":" << Simulator::Now().GetSeconds()
                  << ",\"flow_id\":\"" << flow_label << "\""
                  << ",\"target_flow\":\"" << g_sionna_target_flow << "\""
                  << ",\"loss_ratio\":" << g_sionna_last_loss
                  << ",\"extra_delay_ms\":" << g_sionna_last_delay_ms
                  << ",\"channel_delay_ms\":" << logged_total_delay_ms
                  << ",\"run_id\":\"" << runId << "\"}";
                emit_event(o.str());
            }
        }
    }
    Simulator::Schedule(MilliSeconds(100), &sionna_poll_tick, runId);
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

    // 2.1.d: сохраняем pointer к CsmaChannel для динамического обновления
    // delay (Sionna RT extra_delay_ms). devs.Get(0)->GetChannel() возвращает
    // Ptr<Channel>; cast'им в CsmaChannel чтобы менять `Delay` attribute.
    Ptr<CsmaChannel> csma_ch = devs.Get(0)->GetChannel()->GetObject<CsmaChannel>();
    g_csma_channels[p.name] = csma_ch;
    g_base_delay_ms[p.name] = p.delay_ms;

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

    // Этап 2.1.d: путь к /tmp/sionna_channel.json (динамический loss_ratio
    // от sionna_channel_publisher.py). Пусто = не использовать.
    cmd.AddValue("sionnaChannelPath",
                 "путь к JSON-файлу с актуальным Sionna loss_ratio (poll 10 Hz)",
                 g_sionna_path);
    // Roadmap backlog: target_flow для Sionna live hook.
    //   payload (default) — только видео, как было раньше
    //   control           — только MAVLink-команды (демо NLOS без визуала)
    //   both              — оба канала; за зданием падает и видео, и
    //                       управление, как в реальной радио-физике
    cmd.AddValue("sionnaTargetFlow",
                 "куда применять Sionna update: payload|control|both",
                 g_sionna_target_flow);

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

    // Sionna RT poll (если задан --sionnaChannelPath).
    if (!g_sionna_path.empty()) {
        // Нормализуем target_flow до известного значения (UI/orchestrator
        // могут передать что угодно через CLI). Default — payload.
        if (g_sionna_target_flow != "control" &&
            g_sionna_target_flow != "payload" &&
            g_sionna_target_flow != "both") {
            NS_LOG_UNCOND("[sionna] unknown target_flow=\"" << g_sionna_target_flow
                          << "\", falling back to \"payload\"");
            g_sionna_target_flow = "payload";
        }
        NS_LOG_UNCOND("[sionna] poll path=" << g_sionna_path
                      << " interval=100ms target=" << g_sionna_target_flow);
        // Эмитим explicit start-event с target_flow, чтобы analyzer/отчёт
        // мог пометить run как "Sionna applied to both/control/payload".
        std::ostringstream o;
        o << "{\"event_type\":\"component\","
          << "\"component\":\"ns3:sionna_poll\","
          << "\"phase\":\"start\","
          << "\"target_flow\":\"" << g_sionna_target_flow << "\","
          << "\"channel_path\":\"" << escape_json(g_sionna_path) << "\","
          << "\"run_id\":\"" << runId << "\"}";
        emit_event(o.str());

        Simulator::Schedule(MilliSeconds(100), &sionna_poll_tick, runId);
    }

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
