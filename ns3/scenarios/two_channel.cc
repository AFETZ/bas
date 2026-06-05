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

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <limits>
#include <map>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

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
static std::ofstream g_packet_audit_csv;
static std::string g_packet_audit_csv_path = "";
static uint32_t g_packet_audit_seed = 1337;
static std::string g_run_id = "";

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

static std::string
escape_csv(const std::string& value)
{
    bool needs_quotes = value.find_first_of(",\"\n\r") != std::string::npos;
    if (!needs_quotes) {
        return value;
    }
    std::string out = "\"";
    for (char c : value) {
        if (c == '"') {
            out += "\"\"";
        } else {
            out += c;
        }
    }
    out += "\"";
    return out;
}

static bool
finite_or_nan(double value)
{
    return std::isfinite(value);
}

static void
write_csv_number(std::ostream& os, double value, int precision = 9)
{
    if (finite_or_nan(value)) {
        os << std::fixed << std::setprecision(precision) << value;
    }
}

static double
RepositoryLossMapping(double rssiDb)
{
    return 1.0 / (1.0 + std::exp(0.5 * (rssiDb + 78.0)));
}

struct RtpProbe {
    bool found = false;
    uint16_t sequence = 0;
    uint32_t timestamp_90khz = 0;
    uint32_t ssrc = 0;
    uint32_t offset = 0;
};

static RtpProbe
find_rtp_header(Ptr<Packet> packet)
{
    RtpProbe probe;
    const uint32_t packetSize = packet->GetSize();
    const uint32_t maxBytes = std::min<uint32_t>(packetSize, 256);
    if (maxBytes < 12) {
        return probe;
    }

    std::vector<uint8_t> data(maxBytes);
    packet->CopyData(data.data(), maxBytes);
    for (uint32_t off = 0; off + 12 <= maxBytes; ++off) {
        const uint8_t first = data[off];
        const uint8_t version = first >> 6;
        const uint8_t csrcCount = first & 0x0f;
        const uint8_t payloadType = data[off + 1] & 0x7f;
        const uint32_t headerLen = 12 + 4 * static_cast<uint32_t>(csrcCount);
        if (version != 2 || payloadType != 96 || off + headerLen > maxBytes) {
            continue;
        }
        probe.found = true;
        probe.sequence = static_cast<uint16_t>((data[off + 2] << 8) | data[off + 3]);
        probe.timestamp_90khz =
            (static_cast<uint32_t>(data[off + 4]) << 24) |
            (static_cast<uint32_t>(data[off + 5]) << 16) |
            (static_cast<uint32_t>(data[off + 6]) << 8) |
            static_cast<uint32_t>(data[off + 7]);
        probe.ssrc =
            (static_cast<uint32_t>(data[off + 8]) << 24) |
            (static_cast<uint32_t>(data[off + 9]) << 16) |
            (static_cast<uint32_t>(data[off + 10]) << 8) |
            static_cast<uint32_t>(data[off + 11]);
        probe.offset = off;
        return probe;
    }
    return probe;
}

struct SionnaSample {
    bool valid = false;
    uint64_t sequence = 0;
    double ns3_update_time_s = std::numeric_limits<double>::quiet_NaN();
    double source_wall_time = std::numeric_limits<double>::quiet_NaN();
    double source_sim_time = std::numeric_limits<double>::quiet_NaN();
    double rssi_db = std::numeric_limits<double>::quiet_NaN();
    double path_loss_db = std::numeric_limits<double>::quiet_NaN();
    double expected_loss = std::numeric_limits<double>::quiet_NaN();
    double extra_delay_ms = std::numeric_limits<double>::quiet_NaN();
    double uav_lat = std::numeric_limits<double>::quiet_NaN();
    double uav_lon = std::numeric_limits<double>::quiet_NaN();
    double uav_alt_rel_m = std::numeric_limits<double>::quiet_NaN();
    double uav_x_north = std::numeric_limits<double>::quiet_NaN();
    double uav_y_east = std::numeric_limits<double>::quiet_NaN();
    double uav_z_m = std::numeric_limits<double>::quiet_NaN();
    double gcs_x_north = std::numeric_limits<double>::quiet_NaN();
    double gcs_y_east = std::numeric_limits<double>::quiet_NaN();
    double gcs_z_m = std::numeric_limits<double>::quiet_NaN();
    std::string channel_model;
};

class AuditedRateErrorModel : public ErrorModel
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::AuditedRateErrorModel")
            .SetParent<ErrorModel>()
            .SetGroupName("Network")
            .AddConstructor<AuditedRateErrorModel>()
            .AddAttribute("ErrorRate",
                          "Packet error probability used by the audited model.",
                          DoubleValue(0.0),
                          MakeDoubleAccessor(&AuditedRateErrorModel::m_errorRate),
                          MakeDoubleChecker<double>(0.0, 1.0));
        return tid;
    }

    AuditedRateErrorModel()
        : m_rng(CreateObject<UniformRandomVariable>())
    {
    }

    void Configure(std::string runId,
                   std::string flowId,
                   std::string channelType,
                   std::string direction,
                   uint32_t txNode,
                   uint32_t rxNode,
                   uint32_t seed)
    {
        m_runId = std::move(runId);
        m_flowId = std::move(flowId);
        m_channelType = std::move(channelType);
        m_direction = std::move(direction);
        m_txNode = txNode;
        m_rxNode = rxNode;
        m_seed = seed;
    }

    int64_t AssignStream(int64_t stream)
    {
        m_rng->SetStream(stream);
        return 1;
    }

    void SetErrorRate(double rate, std::string reason)
    {
        m_errorRate = std::max(0.0, std::min(1.0, rate));
        m_rateReason = std::move(reason);
    }

    void SetSionnaSample(const SionnaSample& sample)
    {
        m_sample = sample;
    }

  private:
    bool DoCorrupt(Ptr<Packet> packet) override
    {
        const double draw = m_rng->GetValue(0.0, 1.0);
        const bool dropped = draw < m_errorRate;
        const char* finalDecision = dropped ? "dropped" : "received";
        std::string dropReason = "none";
        if (dropped) {
            dropReason = (m_rateReason == "outage") ? "outage" : "phy_rf_bernoulli";
        }

        if (g_packet_audit_csv.is_open()) {
            const RtpProbe rtp = find_rtp_header(packet);
            double rssiFromSionna = m_sample.valid ? m_sample.rssi_db
                : std::numeric_limits<double>::quiet_NaN();
            double rssiUsed = rssiFromSionna;
            double expectedLoss = std::isfinite(rssiUsed)
                ? RepositoryLossMapping(rssiUsed)
                : m_errorRate;

            g_packet_audit_csv
                << escape_csv(m_runId) << ","
                << packet->GetUid() << ",";
            write_csv_number(g_packet_audit_csv, Simulator::Now().GetSeconds());
            g_packet_audit_csv << ","
                << escape_csv(m_flowId) << ","
                << escape_csv(m_channelType) << ","
                << m_txNode << ","
                << m_rxNode << ","
                << escape_csv(m_direction) << ","
                << packet->GetSize() << ",";
            if (rtp.found) {
                g_packet_audit_csv << rtp.sequence << ","
                                   << rtp.timestamp_90khz << ","
                                   << rtp.timestamp_90khz << ","
                                   << rtp.offset;
            } else {
                g_packet_audit_csv << ",,,";
            }
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.uav_x_north, 6);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.uav_y_east, 6);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.uav_z_m, 6);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.uav_lat, 9);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.uav_lon, 9);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.uav_alt_rel_m, 6);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.gcs_x_north, 6);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.gcs_y_east, 6);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.gcs_z_m, 6);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, rssiFromSionna, 6);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, rssiUsed, 6);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, expectedLoss, 9);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_errorRate, 9);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, draw, 9);
            g_packet_audit_csv << ","
                << finalDecision << ","
                << escape_csv(dropReason) << ","
                << "ReceiveErrorModel::DoCorrupt" << ","
                << m_seed << ","
                << (m_sample.valid ? "true" : "false") << ","
                << m_sample.sequence << ","
                << escape_csv(m_sample.channel_model) << ",";
            write_csv_number(g_packet_audit_csv, m_sample.source_wall_time, 9);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.source_sim_time, 9);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.ns3_update_time_s, 9);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.path_loss_db, 6);
            g_packet_audit_csv << ",";
            write_csv_number(g_packet_audit_csv, m_sample.extra_delay_ms, 6);
            g_packet_audit_csv << ","
                << escape_csv(m_rateReason)
                << "\n";
        }

        return dropped;
    }

    void DoReset() override
    {
    }

    Ptr<UniformRandomVariable> m_rng;
    std::string m_runId;
    std::string m_flowId;
    std::string m_channelType;
    std::string m_direction;
    std::string m_rateReason = "base_profile";
    uint32_t m_txNode = 0;
    uint32_t m_rxNode = 0;
    uint32_t m_seed = 0;
    double m_errorRate = 0.0;
    SionnaSample m_sample;
};

// -----------------------------------------------------------------------------
// Trace callbacks.
// -----------------------------------------------------------------------------
static std::map<std::string, ChannelStats> g_stats;
static std::map<std::string, std::vector<Ptr<AuditedRateErrorModel>>> g_error_models;
static std::map<std::string, Ptr<CsmaChannel>> g_csma_channels;   // 2.1.d: для dynamic delay
static std::map<std::string, double> g_base_loss;
static std::map<std::string, double> g_base_delay_ms;             // 2.1.d: base delay из CLI

static void
set_error_rate_for_channel(const std::string& ch, double rate, const std::string& reason)
{
    auto it = g_error_models.find(ch);
    if (it == g_error_models.end()) return;
    for (auto& em : it->second) {
        if (em) {
            em->SetErrorRate(rate, reason);
        }
    }
}

static void
set_sionna_sample_for_channel(const std::string& ch, const SionnaSample& sample)
{
    auto it = g_error_models.find(ch);
    if (it == g_error_models.end()) return;
    for (auto& em : it->second) {
        if (em) {
            em->SetSionnaSample(sample);
        }
    }
}

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

static bool
parse_sionna_number(const std::string& body, const std::string& key_name, double& out) {
    std::string key = "\"" + key_name + "\":";
    auto pos = body.find(key);
    if (pos == std::string::npos) return false;
    pos += key.size();
    char* endp = nullptr;
    double val = std::strtod(body.c_str() + pos, &endp);
    if (endp == body.c_str() + pos) return false;
    out = val;
    return true;
}

static std::string
parse_sionna_string(const std::string& body, const std::string& key_name) {
    std::string key = "\"" + key_name + "\":";
    auto pos = body.find(key);
    if (pos == std::string::npos) return "";
    pos += key.size();
    while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos]))) {
        ++pos;
    }
    if (pos >= body.size() || body[pos] != '"') return "";
    ++pos;
    std::string out;
    while (pos < body.size()) {
        char c = body[pos++];
        if (c == '"') break;
        if (c == '\\' && pos < body.size()) {
            out += body[pos++];
        } else {
            out += c;
        }
    }
    return out;
}

static SionnaSample
parse_sionna_sample(const std::string& body, double loss, double extra_delay_ms)
{
    static uint64_t sample_sequence = 0;
    SionnaSample sample;
    double parsed = 0.0;
    if (parse_sionna_number(body, "rss_db", parsed) ||
        parse_sionna_number(body, "rssi_db", parsed)) {
        sample.rssi_db = parsed;
        sample.valid = true;
    }
    if (parse_sionna_number(body, "path_loss_db", parsed)) {
        sample.path_loss_db = parsed;
    }
    if (parse_sionna_number(body, "wall_time", parsed)) {
        sample.source_wall_time = parsed;
    }
    if (parse_sionna_number(body, "sim_time", parsed)) {
        sample.source_sim_time = parsed;
    }
    if (parse_sionna_number(body, "uav_lat", parsed)) {
        sample.uav_lat = parsed;
    }
    if (parse_sionna_number(body, "uav_lon", parsed)) {
        sample.uav_lon = parsed;
    }
    if (parse_sionna_number(body, "uav_alt_rel_m", parsed)) {
        sample.uav_alt_rel_m = parsed;
        sample.uav_z_m = parsed;
    }
    if (parse_sionna_number(body, "uav_x_north", parsed) ||
        parse_sionna_number(body, "x_m", parsed)) {
        sample.uav_x_north = parsed;
    }
    if (parse_sionna_number(body, "uav_y_east", parsed) ||
        parse_sionna_number(body, "y_m", parsed)) {
        sample.uav_y_east = parsed;
    }
    if (parse_sionna_number(body, "uav_z_m", parsed) ||
        parse_sionna_number(body, "z_m", parsed)) {
        sample.uav_z_m = parsed;
    }
    if (parse_sionna_number(body, "gcs_x_north", parsed) ||
        parse_sionna_number(body, "tx_x_north", parsed)) {
        sample.gcs_x_north = parsed;
    }
    if (parse_sionna_number(body, "gcs_y_east", parsed) ||
        parse_sionna_number(body, "tx_y_east", parsed)) {
        sample.gcs_y_east = parsed;
    }
    if (parse_sionna_number(body, "gcs_z_m", parsed) ||
        parse_sionna_number(body, "tx_z_m", parsed)) {
        sample.gcs_z_m = parsed;
    }
    sample.expected_loss = loss;
    sample.extra_delay_ms = extra_delay_ms;
    sample.channel_model = parse_sionna_string(body, "channel_model");
    sample.ns3_update_time_s = Simulator::Now().GetSeconds();
    sample.sequence = ++sample_sequence;
    return sample;
}

static void
sionna_poll_tick(std::string runId) {
    if (!g_sionna_path.empty()) {
        std::ifstream f(g_sionna_path);
        if (f.is_open()) {
            std::string body((std::istreambuf_iterator<char>(f)),
                             std::istreambuf_iterator<char>());

            // ---- loss_ratio ----
            double loss = parse_sionna_field(body, "loss_ratio");
            double extra_delay_ms = parse_sionna_field(body, "extra_delay_ms");
            SionnaSample sample = parse_sionna_sample(body, loss, extra_delay_ms);
            auto targets = sionna_target_channels();
            bool loss_changed = false;
            if (loss >= 0.0 && loss <= 1.0) {
                for (const auto& ch : targets) {
                    set_sionna_sample_for_channel(ch, sample);
                    // Не перетираем outage-блокировку: outage кладёт ErrorRate=1.0.
                    if (!g_stats[ch].in_outage) {
                        set_error_rate_for_channel(ch, loss, "sionna_rt_loss");
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
    set_error_rate_for_channel(ch, 1.0, "outage");
    g_stats[ch].in_outage = true;

    std::ostringstream o;
    o << "{\"event_type\":\"component\",\"component\":\"ns3:" << ch
      << "\",\"phase\":\"outage_begin\",\"sim_time\":" << Simulator::Now().GetSeconds() << "}";
    emit_event(o.str());
}

static void
outage_end(std::string ch) {
    set_error_rate_for_channel(ch, g_base_loss[ch], "base_profile");
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

    // Error model на обеих сторонах (симметричная потеря). Используем
    // кастомный ErrorModel: он повторяет packet-level Bernoulli semantics и
    // дополнительно пишет per-packet audit rows, если включён packetAuditCsv.
    Ptr<AuditedRateErrorModel> em_near = CreateObject<AuditedRateErrorModel>();
    em_near->Configure(g_run_id,
                       p.name,
                       p.name,
                       "far_to_near",
                       nodes.Get(1)->GetId(),
                       nodes.Get(0)->GetId(),
                       g_packet_audit_seed);
    em_near->SetErrorRate(p.packet_loss_ratio, "base_profile");
    em_near->AssignStream(static_cast<int64_t>(nodes.Get(0)->GetId() + 1));

    Ptr<AuditedRateErrorModel> em_far = CreateObject<AuditedRateErrorModel>();
    em_far->Configure(g_run_id,
                      p.name,
                      p.name,
                      "near_to_far",
                      nodes.Get(0)->GetId(),
                      nodes.Get(1)->GetId(),
                      g_packet_audit_seed);
    em_far->SetErrorRate(p.packet_loss_ratio, "base_profile");
    em_far->AssignStream(static_cast<int64_t>(nodes.Get(1)->GetId() + 1));

    devs.Get(0)->SetAttribute("ReceiveErrorModel", PointerValue(em_near));
    devs.Get(1)->SetAttribute("ReceiveErrorModel", PointerValue(em_far));
    g_error_models[p.name] = {em_near, em_far};
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
    cmd.AddValue("packetAuditCsv",
                 "optional CSV path for per-packet route-level RSSI/drop audit",
                 g_packet_audit_csv_path);
    cmd.AddValue("packetAuditSeed",
                 "RNG seed recorded in per-packet audit rows",
                 g_packet_audit_seed);

    cmd.Parse(argc, argv);
    g_run_id = runId;
    RngSeedManager::SetSeed(g_packet_audit_seed);
    RngSeedManager::SetRun(1);

    // Открыть JSONL-журнал.
    std::string log_path = logDir + "/" + runId + "/ns3_events.jsonl";
    g_jsonl.open(log_path, std::ios::out | std::ios::app);
    if (!g_jsonl.is_open()) {
        std::cerr << "Cannot open " << log_path << "\n";
        return 1;
    }

    if (!g_packet_audit_csv_path.empty()) {
        g_packet_audit_csv.open(g_packet_audit_csv_path, std::ios::out | std::ios::trunc);
        if (!g_packet_audit_csv.is_open()) {
            std::cerr << "Cannot open packet audit CSV " << g_packet_audit_csv_path << "\n";
            return 1;
        }
        g_packet_audit_csv
            << "run_id,packet_uid,timestamp,flow_id,channel_type,tx_node,rx_node,"
            << "direction,packet_size,rtp_sequence_number,rtp_timestamp_90khz,"
            << "frame_id,rtp_parse_offset,uav_x_north,uav_y_east,uav_z_m,uav_lat,uav_lon,"
            << "uav_alt_rel_m,gcs_x_north,gcs_y_east,gcs_z_m,"
            << "RSSI_from_Sionna,RSSI_used_for_drop_decision,"
            << "expected_p_loss,error_rate_used_for_drop,random_draw,"
            << "final_decision,drop_reason,ns3_trace_source,seed,"
            << "sionna_sample_valid,sionna_sample_sequence,sionna_channel_model,"
            << "sionna_source_wall_time,sionna_source_sim_time,ns3_sionna_update_time,"
            << "path_loss_db,extra_delay_ms,error_rate_reason\n";
    }

    // Стартовое событие.
    {
        std::ostringstream o;
        o << "{\"event_type\":\"component\",\"component\":\"ns3\",\"phase\":\"start\""
          << ",\"run_id\":\"" << runId << "\""
          << ",\"duration_s\":" << duration_s
          << ",\"packet_audit_csv\":\"" << escape_json(g_packet_audit_csv_path) << "\""
          << ",\"packet_audit_seed\":" << g_packet_audit_seed
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
    if (g_packet_audit_csv.is_open()) {
        g_packet_audit_csv.close();
    }

    Simulator::Destroy();
    return 0;
}
