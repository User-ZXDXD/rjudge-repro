"""拉取 R-Judge 官方仓库中本复现需要的数据与评测结果。

本仓库不重新分发官方数据集，而是从上游按需拉取（理由见 README 第十节）。
只取两份东西：

    upstream/R-Judge-main/data/                             官方数据集（5 个场景）
    upstream/R-Judge-main/eval/results/overall_result_unsafe.json
                                                            官方 GPT-4 的分析结果，
                                                            风险识别测试要拿它当参照

用法（在仓库根目录执行）：

    python scripts/fetch_upstream.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

REPO = "https://github.com/Lordog/R-Judge.git"

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(HERE, "upstream", "R-Judge-main")

# 上游仓库里本复现真正用到的路径，其余（assets / cache / results）一概不取
WANTED = [
    "data",
    os.path.join("eval", "results", "overall_result_unsafe.json"),
]


def already_there():
    return os.path.isdir(os.path.join(DEST, "data"))


def main():
    if already_there():
        print("已存在，跳过拉取：", DEST)
        print("（想强制重取，先手动删掉 upstream/ 目录）")
        return 0

    tmp = tempfile.mkdtemp(prefix="rjudge_upstream_")
    try:
        print("正在克隆上游仓库（浅克隆，只取最新一次提交）...")
        subprocess.check_call(
            ["git", "clone", "--depth", "1", REPO, tmp],
            stdout=subprocess.DEVNULL,
        )

        os.makedirs(DEST, exist_ok=True)
        for rel in WANTED:
            src = os.path.join(tmp, rel)
            dst = os.path.join(DEST, rel)
            if not os.path.exists(src):
                raise RuntimeError("上游仓库里找不到：" + rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
            print("  ->", rel)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("完成。下一步：python rj_client.py  （冒烟测试，应打印 reply = 'OK'）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
