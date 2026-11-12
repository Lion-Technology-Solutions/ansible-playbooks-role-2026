"""Keep developer/test requirements aligned with Vercel's runtime dependency manifest."""
import tomllib
from pathlib import Path


def test_deployment_and_local_dependency_manifests_match():
    root=Path(__file__).resolve().parents[1]
    deployed=set(tomllib.loads((root/'pyproject.toml').read_text())['project']['dependencies'])
    local={line.strip() for line in (root/'requirements.txt').read_text().splitlines() if line.strip() and not line.strip().startswith('#')}
    assert deployed==local, 'Vercel installs pyproject.toml; keep requirements.txt in sync to avoid untested runtime imports.'
