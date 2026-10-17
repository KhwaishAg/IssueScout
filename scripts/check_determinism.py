"""Diagnostic: does the same configuration give the same ranking every time?

Runs one configuration on the dev split repeatedly, then once more after wiping the
collection and its sync state (a cold rebuild), and reports every instance whose ranked
files differ. FR-RET-12 requires identical rankings for an identical index and query.

Usage: uv run python scripts/check_determinism.py [A0|A1]
"""

import sys

from issuescout.evaluation.configs import CONFIGS
from issuescout.evaluation.prepare import prepare_split
from issuescout.evaluation.runner import InstanceResult, run_config
from issuescout.settings import get_settings
from issuescout.storage.qdrant import make_client


def ranks(result: list[InstanceResult]) -> dict[str, tuple[str, ...]]:
    return {i.instance_id: i.ranked_files for i in result}


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "A0"
    cfg = CONFIGS[name]
    settings = get_settings()
    client = make_client(settings)
    prepared = prepare_split("dev", settings)

    def run() -> dict[str, tuple[str, ...]]:
        res = run_config(
            prepared, settings, config=cfg, split="dev", client=client, encoder=None, cache=None
        )
        return ranks(res.instances)

    first, second = run(), run()
    if client.collection_exists(cfg.collection):
        client.delete_collection(cfg.collection)
    state = settings.state_dir / "eval" / f"{cfg.collection}.json"
    state.unlink(missing_ok=True)
    cold = run()

    for label, other in (("repeat run", second), ("cold rebuild", cold)):
        diff = [k for k in first if first[k] != other[k]]
        print(f"{label}: {len(diff)} of {len(first)} instances differ")
        for k in diff[:5]:
            a, b = first[k], other[k]
            pos = next((i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), None)
            print(f"  {k}: first difference at rank {None if pos is None else pos + 1}")
            if pos is not None:
                print(f"    first : {a[pos]}  (list length {len(a)})")
                print(f"    {label[:5]}: {b[pos]}  (list length {len(b)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
