import glob, json, collections, re, random, sys
sys.path.insert(0, r"C:/Users/Pichau/OneDrive/Documentos/tcc - minas/tcc_minas/agents/benchmark")
from intent_set import INTENTS
REASON = {i["id"]: i.get("invalid_reason", "") for i in INTENTS}
R = r"C:/Users/Pichau/OneDrive/Documentos/tcc - minas/tcc_minas/agents/benchmark/results"
D = collections.defaultdict(list)
for p in glob.glob(R + "/run2/*.jsonl") + glob.glob(R + "/rounds/r[0-9]/*.jsonl"):
    for l in open(p, encoding="utf-8"):
        r = json.loads(l); D[r["condition"]].append(r)

GENERIC = re.compile(r"not found in the retrieved context|unable to find information|rephras", re.I)
CUE = {
    "sst": re.compile(r"\bSST\b|slice type|not provisioned|IoT|MIoT|fatia", re.I),
    "thp": re.compile(r"\b0\s*Mbps|-\s?5\s*Mbps|zero|negative|positive|greater than|maior que|> ?0|non-positive", re.I),
    "win": re.compile(r"end(?:s|ing)? (?:time )?(?:is )?before|before (?:the )?start|after (?:the )?(?:end|start)|earlier than|window|janela|in[ií]cio|22:00.*18:00|9 ?PM.*6 ?PM", re.I),
}
NEG = re.compile(r"\bnot\b|cannot|can't|unable|invalid|must|only|unsupported|n[aã]o\b|deve|inv[aá]lid|reject|refus|error|clarify", re.I)
ASK = re.compile(r"please (?:provide|specify|clarify|confirm)|could you|can you (?:provide|specify|clarify|confirm)|would you like|do you want|por favor", re.I)

def kind(i):
    r = REASON[i]
    if "SST" in r: return "sst"
    if "non-positive" in r: return "thp"
    return "win"

def classify(r):
    if r["db_records"]:
        return "recorded_valid_value"
    t = r.get("final_text") or ""
    if CUE[kind(r["id"])].search(t) and NEG.search(t):
        return "refused_explained"
    if ASK.search(t) and "?" in t:
        return "asked_clarification"
    return "no_action"

samples = []
for c in ["qwen2.5-7b_rag-true_guardrails-true", "llama3.1-8b_rag-true_guardrails-true",
          "otel-llm-e4b-it-latest_rag-true_guardrails-true"]:
    cnt = collections.Counter()
    for r in D[c]:
        if r["validity"] == "invalid" and r["task_success"]:
            k = classify(r); cnt[k] += 1
            samples.append((c[:5], k, r["id"], (r.get("final_text") or "")[:220].replace("\n", " ")))
    print(c, sum(cnt.values()), dict(cnt))
random.Random(7).shuffle(samples)
print("\n--- spot check ---")
for s in [x for x in samples if x[0] != "otel-" and x[1] != "recorded_valid_value"][:24]:
    print(s)
