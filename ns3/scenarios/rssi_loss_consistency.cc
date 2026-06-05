// rssi_loss_consistency.cc - controlled packet-level ns-3 RSSI/loss check.
//
// This scenario is intentionally independent from Gazebo/SITL trajectories.
// It verifies whether packet-level Bernoulli loss decisions match the
// repository RSSI->loss mapping for fixed RSSI targets and independent seeds.

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/csma-module.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("RssiLossConsistency");

static std::ofstream g_packet_csv;

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

static double
RepositoryLossMapping(double rssiDb)
{
    return 1.0 / (1.0 + std::exp(0.5 * (rssiDb + 78.0)));
}

class RssiLossDecisionErrorModel : public ErrorModel
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("RssiLossDecisionErrorModel")
            .SetParent<ErrorModel>()
            .SetGroupName("Network")
            .AddConstructor<RssiLossDecisionErrorModel>();
        return tid;
    }

    RssiLossDecisionErrorModel()
        : m_rng(CreateObject<UniformRandomVariable>())
    {
    }

    void Configure(std::string runId,
                   std::string flowId,
                   std::string channelType,
                   uint32_t txNode,
                   uint32_t rxNode,
                   double rssiTargetDb,
                   uint32_t seed)
    {
        m_runId = std::move(runId);
        m_flowId = std::move(flowId);
        m_channelType = std::move(channelType);
        m_txNode = txNode;
        m_rxNode = rxNode;
        m_rssiTargetDb = rssiTargetDb;
        m_rssiUsedDb = rssiTargetDb;
        m_expectedLoss = RepositoryLossMapping(rssiTargetDb);
        m_seed = seed;
    }

    int64_t AssignStream(int64_t stream)
    {
        m_rng->SetStream(stream);
        return 1;
    }

  private:
    bool DoCorrupt(Ptr<Packet> packet) override
    {
        const double draw = m_rng->GetValue(0.0, 1.0);
        const bool dropped = draw < m_expectedLoss;
        const char* finalDecision = dropped ? "dropped" : "received";
        const char* dropReason = dropped ? "phy_rf_bernoulli" : "none";

        if (g_packet_csv.is_open()) {
            g_packet_csv << m_runId << ","
                         << packet->GetUid() << ","
                         << std::fixed << std::setprecision(9)
                         << Simulator::Now().GetSeconds() << ","
                         << escape_csv(m_flowId) << ","
                         << escape_csv(m_channelType) << ","
                         << m_txNode << ","
                         << m_rxNode << ","
                         << std::setprecision(6)
                         << m_rssiUsedDb << ","
                         << m_expectedLoss << ","
                         << std::setprecision(9)
                         << draw << ","
                         << finalDecision << ","
                         << dropReason << ","
                         << m_seed << ","
                         << std::setprecision(6)
                         << m_rssiTargetDb << "\n";
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
    uint32_t m_txNode = 0;
    uint32_t m_rxNode = 0;
    double m_rssiTargetDb = 0.0;
    double m_rssiUsedDb = 0.0;
    double m_expectedLoss = 0.0;
    uint32_t m_seed = 0;
};

struct FlowConfig
{
    std::string flowId;
    std::string channelType;
    double bandwidthMbps = 20.0;
    double delayMs = 1.0;
};

static std::vector<double>
ParseDoubleList(const std::string& csv)
{
    std::vector<double> out;
    std::stringstream ss(csv);
    std::string item;
    while (std::getline(ss, item, ',')) {
        if (!item.empty()) {
            out.push_back(std::stod(item));
        }
    }
    return out;
}

static std::vector<FlowConfig>
ParseFlows(const std::string& csv)
{
    std::vector<FlowConfig> out;
    std::stringstream ss(csv);
    std::string item;
    while (std::getline(ss, item, ',')) {
        if (item == "control") {
            out.push_back({"control", "control", 20.0, 1.0});
        } else if (item == "payload") {
            out.push_back({"payload", "payload", 20.0, 1.0});
        }
    }
    return out;
}

static void
SendPacket(Ptr<NetDevice> txDev, Address dst, uint32_t packetBytes)
{
    Ptr<Packet> packet = Create<Packet>(packetBytes);
    txDev->Send(packet, dst, 0x88B5);
}

int
main(int argc, char* argv[])
{
    std::string runId = "rssi_loss_consistency";
    std::string outCsv = "/work/logs/rssi_loss_consistency_per_packet.csv";
    std::string targetsCsv = "-95,-90,-85,-82,-80,-78,-76,-74,-72,-70,-68,-65,-60";
    std::string flowsCsv = "control,payload";
    uint32_t seeds = 5;
    uint32_t packetsPerPoint = 1000;
    uint32_t packetBytes = 256;
    double intervalMs = 0.2;
    uint32_t baseSeed = 1337;

    CommandLine cmd(__FILE__);
    cmd.AddValue("runId", "Run id written into CSV rows", runId);
    cmd.AddValue("outCsv", "Per-packet CSV output path", outCsv);
    cmd.AddValue("targets",
                 "Comma-separated RSSI targets in dBm",
                 targetsCsv);
    cmd.AddValue("flows", "Comma-separated flows: control,payload", flowsCsv);
    cmd.AddValue("seeds", "Independent seed count", seeds);
    cmd.AddValue("packetsPerPoint", "Packets per RSSI target per seed per flow", packetsPerPoint);
    cmd.AddValue("packetBytes", "Generated packet payload size", packetBytes);
    cmd.AddValue("intervalMs", "Inter-packet interval per flow/target/seed", intervalMs);
    cmd.AddValue("baseSeed", "ns-3 RNG base seed", baseSeed);
    cmd.Parse(argc, argv);

    auto targets = ParseDoubleList(targetsCsv);
    auto flows = ParseFlows(flowsCsv);
    if (targets.empty()) {
        std::cerr << "No RSSI targets configured\n";
        return 2;
    }
    if (flows.empty()) {
        std::cerr << "No flows configured\n";
        return 2;
    }
    if (seeds == 0 || packetsPerPoint == 0) {
        std::cerr << "seeds and packetsPerPoint must be positive\n";
        return 2;
    }

    RngSeedManager::SetSeed(baseSeed);
    RngSeedManager::SetRun(1);

    g_packet_csv.open(outCsv, std::ios::out | std::ios::trunc);
    if (!g_packet_csv.is_open()) {
        std::cerr << "Cannot open " << outCsv << "\n";
        return 1;
    }
    g_packet_csv << "run_id,packet_uid,timestamp,flow_id,channel_type,tx_node,rx_node,"
                 << "rssi_db_used,expected_p_loss,random_draw,final_decision,"
                 << "drop_reason,seed,rssi_target\n";

    std::vector<NodeContainer> nodeContainers;
    std::vector<NetDeviceContainer> deviceContainers;
    std::vector<Ptr<RssiLossDecisionErrorModel>> errorModels;
    nodeContainers.reserve(flows.size() * targets.size() * seeds);
    deviceContainers.reserve(flows.size() * targets.size() * seeds);
    errorModels.reserve(flows.size() * targets.size() * seeds);

    uint32_t combo = 0;
    double maxStopS = 0.0;
    constexpr double kSettleSeconds = 5.0;
    for (const auto& flow : flows) {
        for (double target : targets) {
            for (uint32_t seed = 1; seed <= seeds; ++seed) {
                NodeContainer nodes;
                nodes.Create(2);

                CsmaHelper csma;
                std::ostringstream rate;
                rate << static_cast<uint64_t>(flow.bandwidthMbps * 1'000'000) << "bps";
                csma.SetChannelAttribute("DataRate", StringValue(rate.str()));
                csma.SetChannelAttribute("Delay", TimeValue(MilliSeconds(flow.delayMs)));
                csma.SetQueue("ns3::DropTailQueue", "MaxSize", StringValue("20000p"));

                NetDeviceContainer devs = csma.Install(nodes);
                Ptr<RssiLossDecisionErrorModel> em = CreateObject<RssiLossDecisionErrorModel>();
                em->Configure(runId,
                              flow.flowId,
                              flow.channelType,
                              nodes.Get(0)->GetId(),
                              nodes.Get(1)->GetId(),
                              target,
                              seed);
                em->AssignStream(static_cast<int64_t>(combo + 1));
                devs.Get(1)->SetAttribute("ReceiveErrorModel", PointerValue(em));

                const double startS = 0.01 + combo * 0.00001;
                const double intervalS = intervalMs / 1000.0;
                for (uint32_t i = 0; i < packetsPerPoint; ++i) {
                    Simulator::Schedule(Seconds(startS + i * intervalS),
                                        &SendPacket,
                                        devs.Get(0),
                                        devs.Get(1)->GetAddress(),
                                        packetBytes);
                }
                maxStopS = std::max(maxStopS, startS + packetsPerPoint * intervalS + kSettleSeconds);

                nodeContainers.push_back(nodes);
                deviceContainers.push_back(devs);
                errorModels.push_back(em);
                ++combo;
            }
        }
    }

    NS_LOG_UNCOND("RSSI consistency run: flows=" << flows.size()
                  << " targets=" << targets.size()
                  << " seeds=" << seeds
                  << " packetsPerPoint=" << packetsPerPoint
                  << " totalPackets=" << (flows.size() * targets.size() * seeds * packetsPerPoint));
    Simulator::Stop(Seconds(maxStopS));
    Simulator::Run();
    Simulator::Destroy();
    g_packet_csv.close();
    return 0;
}
