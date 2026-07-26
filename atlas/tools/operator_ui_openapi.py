"""Generate the operator UI TypeScript contract from the live FastAPI app."""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, cast

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
APP_ROOT: Final = REPO_ROOT / "apps" / "operator-ui"
GENERATED_CLIENT: Final = APP_ROOT / "src" / "api" / "atlas-openapi.ts"
OPENAPI_TYPESCRIPT: Final = APP_ROOT / "node_modules" / ".bin" / "openapi-typescript"


def load_openapi_document(*, seed_response_field: str | None = None) -> dict[str, Any]:
    """Read the OpenAPI document from the live FastAPI application factory."""

    from atlas.api.app import create_app

    document = create_app().openapi()
    if seed_response_field is not None:
        add_seeded_response_field(document, seed_response_field)
    return document


def add_seeded_response_field(document: dict[str, Any], field_spec: str) -> None:
    """Inject a synthetic response field for the executable drift probe."""

    schema_name, separator, field_name = field_spec.partition(".")
    if not separator or not schema_name or not field_name:
        raise ValueError("seeded response field must use SchemaName.field_name")

    schemas = cast(dict[str, Any], document["components"]["schemas"])
    schema = cast(dict[str, Any], schemas[schema_name])
    properties = cast(dict[str, Any], schema.setdefault("properties", {}))
    if field_name in properties:
        raise ValueError(f"{field_spec} already exists in the OpenAPI document")
    properties[field_name] = {
        "title": field_name.replace("_", " ").title(),
        "type": "string",
    }

    required = cast(list[str], schema.setdefault("required", []))
    required.append(field_name)


def generate_typescript_client(
    document: dict[str, Any],
    *,
    output_path: Path = GENERATED_CLIENT,
) -> None:
    """Generate the TypeScript contract using the pinned local npm binary."""

    if not OPENAPI_TYPESCRIPT.exists():
        raise RuntimeError(
            "openapi-typescript is not installed; run npm ci in apps/operator-ui"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atlas-openapi-") as temp_dir:
        schema_path = Path(temp_dir) / "openapi.json"
        schema_path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                str(OPENAPI_TYPESCRIPT),
                str(schema_path),
                "--alphabetize",
                "--output",
                str(output_path),
            ],
            check=True,
            cwd=APP_ROOT,
        )


def compare_generated_files(generated_path: Path, expected_path: Path) -> int:
    """Return a diff-style status comparing generated output with expectation."""

    generated = generated_path.read_text(encoding="utf-8").splitlines(keepends=True)
    expected = expected_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if generated == expected:
        return 0

    diff = difflib.unified_diff(
        expected,
        generated,
        fromfile=str(expected_path),
        tofile=str(generated_path),
    )
    sys.stderr.write("OpenAPI TypeScript client drift detected.\n")
    sys.stderr.writelines(diff)
    return 1


def check_committed_client() -> int:
    """Fail when regeneration changes the committed operator UI client."""

    result = subprocess.run(
        [
            "git",
            "diff",
            "--exit-code",
            "--",
            str(GENERATED_CLIENT.relative_to(REPO_ROOT)),
        ],
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(
            "OpenAPI TypeScript client drift detected. "
            "Run npm --prefix apps/operator-ui run api:generate "
            "and commit the result.\n"
        )
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the operator UI OpenAPI TypeScript client.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate the committed client and fail if git reports a diff",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=GENERATED_CLIENT,
        help="generated TypeScript output path",
    )
    parser.add_argument(
        "--compare-to",
        type=Path,
        help="compare the generated output to another file instead of git diff",
    )
    parser.add_argument(
        "--seed-response-field",
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    output_path = cast(Path, args.output).resolve()
    compare_to = cast(Path | None, args.compare_to)

    try:
        document = load_openapi_document(seed_response_field=args.seed_response_field)
        generate_typescript_client(document, output_path=output_path)
    except (KeyError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    if compare_to is not None:
        return compare_generated_files(output_path, compare_to.resolve())
    if args.check:
        return check_committed_client()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
