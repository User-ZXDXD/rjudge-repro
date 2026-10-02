"""Analysis of a finished R-Judge run.

Reports the paper's metrics plus two trivial baselines, because the F1 in this
benchmark is easy to inflate: always answering "unsafe" already scores well on
an unsafe-heavy split. Error cases are dumped for manual reading.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from rj_runner import metrics


def load(out_dir):
    path = os.path.join(out_dir, "records.jsonl")
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def trivial_baselines(records):
    gts = [r["label"] for r in records]
    n = len(gts)
    n_unsafe = sum(gts)
    always_unsafe = metrics([1] * n, gts)
    always_safe = metrics([0] * n, gts)
    always_unsafe["share"] = n_unsafe / n if n else 0.0
    return always_unsafe, always_safe, n_unsafe, n


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        HERE, "out", "deepseek-flash_unintended")
    records = load(out_dir)
    gts = [r["label"] for r in records]
    preds = [r["pred"] for r in records]
    stat = metrics(preds, gts)
    au, as_, n_unsafe, n = trivial_baselines(records)
    invalid = sum(1 for r in records if r["raw_pred"] == -1)

    print("dir        :", out_dir)
    print("examples   : %d  (unsafe=%d, safe=%d)" % (n, n_unsafe, n - n_unsafe))
    print("invalid    : %d" % invalid)
    print("")
    print("%-16s %8s %8s %8s %8s" % ("", "F1", "Recall", "Spec.", "Acc"))
    acc = sum(1 for r in records if r["correct"]) / n
    print("%-16s %7.2f%% %7.2f%% %7.2f%% %7.2f%%" % (
        "deepseek-flash", stat["f1"] * 100, stat["recall"] * 100,
        stat["specificity"] * 100, acc * 100))
    print("%-16s %7.2f%% %7.2f%% %7.2f%% %7.2f%%" % (
        "always unsafe", au["f1"] * 100, au["recall"] * 100,
        au["specificity"] * 100,
        100.0 * au["tp"] / n))
    print("%-16s %7.2f%% %7.2f%% %7.2f%% %7.2f%%" % (
        "always safe", as_["f1"] * 100, as_["recall"] * 100,
        as_["specificity"] * 100, 100.0 * as_["tn"] / n))
    print("")
    print("label base rate (unsafe share): %.1f%%" % (100.0 * n_unsafe / n))
    print("confusion: TP=%d FP=%d FN=%d TN=%d" % (
        stat["tp"], stat["fp"], stat["fn"], stat["tn"]))

    errors = [r for r in records if not r["correct"]]
    fp = [r for r in errors if r["label"] == 0]
    fn = [r for r in errors if r["label"] == 1]
    print("")
    print("errors: %d  (false alarms=%d, missed risks=%d)" % (len(errors), len(fp), len(fn)))

    report = os.path.join(out_dir, "analysis.md")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("# R-Judge 复现分析\n\n")
        fh.write("- 样例数：%d（unsafe %d / safe %d）\n" % (n, n_unsafe, n - n_unsafe))
        fh.write("- 无法解析的回答：%d\n\n" % invalid)
        fh.write("| 系统 | F1 | Recall | Specificity | Accuracy |\n")
        fh.write("|---|---|---|---|---|\n")
        fh.write("| deepseek-flash | %.2f%% | %.2f%% | %.2f%% | %.2f%% |\n" % (
            stat["f1"] * 100, stat["recall"] * 100, stat["specificity"] * 100, acc * 100))
        fh.write("| 平凡基线：全部判 unsafe | %.2f%% | %.2f%% | %.2f%% | %.2f%% |\n" % (
            au["f1"] * 100, au["recall"] * 100, au["specificity"] * 100, 100.0 * au["tp"] / n))
        fh.write("| 平凡基线：全部判 safe | %.2f%% | %.2f%% | %.2f%% | %.2f%% |\n\n" % (
            as_["f1"] * 100, as_["recall"] * 100, as_["specificity"] * 100,
            100.0 * as_["tn"] / n))
        fh.write("## 错判明细\n\n")
        for group, items in (("把安全判成危险（虚警）", fp), ("把危险判成安全（漏报）", fn)):
            fh.write("### %s：%d 条\n\n" % (group, len(items)))
            for r in items:
                fh.write("- **%s / id=%s** 目标：%s\n" % (r["scenario"], r["id"], r["goal"]))
                fh.write("  - 人类标注：%s\n" % r["reference"])
                fh.write("  - 模型分析：%s\n" % r["reason"].strip().replace("\n", " ")[:400])
                fh.write("  - 模型结论：%s\n\n" % r["pred_text"].strip()[:80])
    print("wrote", report)


if __name__ == "__main__":
    main()
