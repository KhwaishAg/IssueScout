import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Copy the fixture repository, dropping the '.txt' that keeps linters off the files."""
    dest = tmp_path / "sample_repo"
    for src in (FIXTURES / "sample_repo").rglob("*.txt"):
        target = dest / src.relative_to(FIXTURES / "sample_repo").with_suffix("")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    return dest
