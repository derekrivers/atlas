"""ATLAS-23 D4: the ATLAS-22 fixture-schema seam is closed — the real
generated Proposal schema renders through the real released template."""

from test_renderer import golden_variables

from atlas.planning import Proposal, render_planner_prompt
from atlas.tools.schemas_export import render_schema


def test_real_proposal_schema_renders_through_v1_1_0() -> None:
    schema_text = render_schema(Proposal)
    variables = golden_variables() | {"proposal_json_schema": schema_text}
    rendered = render_planner_prompt(variables)
    assert rendered.prompt_version == "planner-v1.1.0"
    # The generated schema sits in its template position.
    assert '"ProposalTicket"' in rendered.text
    assert '"planner_notes"' in rendered.text
    assert "<proposal_json_schema>" in rendered.text
