from __future__ import annotations

import json
import os
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections import defaultdict, deque

from cerebras.cloud.sdk import Cerebras

# ---------------------------------------------------------------------------
# 1.  WORKFLOW DATA
# ---------------------------------------------------------------------------

WORKFLOW = {
    "nodes": [
        {"id": "N1",  "actor": "BUREAU DES MARCHES",                                        "shape": "Rectangle", "text": "Estimation Administratif du Projet"},
        {"id": "N2",  "actor": "BUREAU DES MARCHES",                                        "shape": "Rectangle", "text": "Lancement de la Consultation"},
        {"id": "N3",  "actor": "BUREAU DES MARCHES",                                        "shape": "Rectangle", "text": "Publication Avis de consultation sur le site du MC"},
        {"id": "N4",  "actor": "BUREAU DES MARCHES",                                        "shape": "Rectangle", "text": "Attribution provisoire"},
        {"id": "N5",  "actor": "BUREAU DES MARCHES",                                        "shape": "Rectangle", "text": "Attribution Definitive"},
        {"id": "N6",  "actor": "BUREAU DES MARCHES",                                        "shape": "Rectangle", "text": "Signature Contrat"},
        {"id": "N7",  "actor": "PRESIDENT DE LA COMMISSION INTERNE D'OUVERTURE DES PLIS",   "shape": "Rectangle", "text": "Lettre"},
        {"id": "N8",  "actor": "PRESIDENT DE LA COMMISSION INTERNE D'OUVERTURE DES PLIS",   "shape": "Rectangle", "text": "Depot des offres"},
        {"id": "N9",  "actor": "PRESIDENT DE LA COMMISSION INTERNE D'OUVERTURE DES PLIS",   "shape": "Diamond",   "text": "ETUDE"},
        {"id": "N10", "actor": "COMMISSION OUVERTURE DES PLIS",                             "shape": "Rectangle", "text": "PV D'OUVERTURE DES PLIS"},
        {"id": "N11", "actor": "COMMISSION EVALUATION DES OFFRES",                          "shape": "Rectangle", "text": "PV EVALUATION DES OFFRES"},
        {"id": "N12", "actor": "COMMISSION OUVERTURE ET EVALUATION DES OFFRES",             "shape": "Rectangle", "text": "PV OUVERTURE ET EVALUATION DES OFFRES"},
        {"id": "N13", "actor": "DAM",                                                       "shape": "Rectangle", "text": "Distribution"},
        {"id": "N14", "actor": "FIN",                                                       "shape": "Oval",      "text": "Classement"},
    ],
    "edges": [
        {"from": "N1",  "to": "N2",  "label": None},
        {"from": "N2",  "to": "N7",  "label": None},
        {"from": "N2",  "to": "N3",  "label": None},
        {"from": "N7",  "to": "N8",  "label": None},
        {"from": "N8",  "to": "N9",  "label": None},
        {"from": "N9",  "to": "N12", "label": "NON"},
        {"from": "N9",  "to": "N10", "label": "OUI"},
        {"from": "N10", "to": "N11", "label": None},
        {"from": "N11", "to": "N13", "label": None},
        {"from": "N12", "to": "N13", "label": None},
        {"from": "N13", "to": "N4",  "label": None},
        {"from": "N4",  "to": "N5",  "label": None},
        {"from": "N5",  "to": "N6",  "label": None},
    ],
}


# ---------------------------------------------------------------------------
# 2.  TOOL IMPLEMENTATIONS
# ---------------------------------------------------------------------------

def tool_validate_workflow(workflow: dict) -> dict:
    """Validates the workflow JSON for structural integrity."""
    errors: list[str] = []
    node_ids = {n["id"] for n in workflow.get("nodes", [])}

    if not node_ids:
        errors.append("No nodes found.")

    for edge in workflow.get("edges", []):
        if edge["from"] not in node_ids:
            errors.append(f"Edge references unknown source node: {edge['from']}")
        if edge["to"] not in node_ids:
            errors.append(f"Edge references unknown target node: {edge['to']}")

    connected = {e["from"] for e in workflow["edges"]} | {e["to"] for e in workflow["edges"]}
    isolated = node_ids - connected
    if isolated:
        errors.append(f"Isolated nodes (no edges): {sorted(isolated)}")

    return {
        "valid": len(errors) == 0,
        "node_count": len(node_ids),
        "edge_count": len(workflow.get("edges", [])),
        "actors": sorted({n["actor"] for n in workflow["nodes"]}),
        "errors": errors,
    }


def tool_analyze_layout(workflow: dict) -> dict:
    """Assigns swim-lane indices and computes a rough topological order."""
    actors = list(dict.fromkeys(n["actor"] for n in workflow["nodes"]))
    actor_lane = {a: i for i, a in enumerate(actors)}

    in_degree: dict[str, int] = defaultdict(int)
    adjacency: dict[str, list[str]] = defaultdict(list)
    node_ids = [n["id"] for n in workflow["nodes"]]
    
    for nid in node_ids:
        in_degree[nid] = in_degree.get(nid, 0)
    
    for e in workflow["edges"]:
        adjacency[e["from"]].append(e["to"])
        in_degree[e["to"]] += 1

    queue = deque(nid for nid in node_ids if in_degree[nid] == 0)
    topo_order: list[str] = []
    
    while queue:
        nid = queue.popleft()
        topo_order.append(nid)
        for nbr in adjacency[nid]:
            in_degree[nbr] -= 1
            if in_degree[nbr] == 0:
                queue.append(nbr)

    node_positions = {
        nid: {
            "col": topo_order.index(nid),
            "row": actor_lane[next(n["actor"] for n in workflow["nodes"] if n["id"] == nid)]
        }
        for nid in node_ids
    }

    return {
        "actors": actors,
        "actor_lane": actor_lane,
        "topological_order": topo_order,
        "node_positions": node_positions,
    }


def tool_render_mermaid(workflow: dict) -> dict:
    """Generates a Mermaid flowchart definition (swimlanes via subgraphs)."""
    actor_nodes: dict[str, list] = defaultdict(list)

    for n in workflow["nodes"]:
        actor_nodes[n["actor"]].append(n)

    lines = ["flowchart TD"]

    def mermaid_node(n: dict) -> str:
        nid, text, shape = n["id"], n["text"].replace('"', "'"), n["shape"]
        if shape == "Diamond":
            return f'    {nid}{{"{text}"}}'
        elif shape == "Oval":
            return f'    {nid}(("{text}"))'
        else:
            return f'    {nid}["{text}"]'

    for actor, nodes in actor_nodes.items():
        safe = re.sub(r"[^A-Za-z0-9_]", "_", actor)
        lines.append(f'  subgraph {safe}["{actor}"]')
        for n in nodes:
            lines.append("  " + mermaid_node(n))
        lines.append("  end")

    for e in workflow["edges"]:
        arrow = f' -->|"{e["label"]}"| ' if e["label"] else " --> "
        lines.append(f'  {e["from"]}{arrow}{e["to"]}')

    return {"format": "mermaid", "definition": "\n".join(lines)}


def tool_render_graphviz(workflow: dict) -> dict:
    """Generates a Graphviz DOT definition with swim-lane clusters."""
    actor_nodes: dict[str, list] = defaultdict(list)
    for n in workflow["nodes"]:
        actor_nodes[n["actor"]].append(n)

    lines = [
        'digraph workflow {',
        '  rankdir=TB;',
        '  graph [fontname="Helvetica", fontsize=10, bgcolor="#f9f9f9"];',
        '  node  [fontname="Helvetica", fontsize=9, style=filled, fillcolor="#ddeeff"];',
        '  edge  [fontname="Helvetica", fontsize=8];',
    ]

    colors = ["#cce5ff", "#d4edda", "#fff3cd", "#f8d7da", "#e2d9f3", "#d1ecf1", "#fefefe", "#fde2b0"]

    for idx, (actor, nodes) in enumerate(actor_nodes.items()):
        color = colors[idx % len(colors)]
        safe_actor = re.sub(r"[^A-Za-z0-9_]", "_", actor)
        lines.append(f'  subgraph cluster_{safe_actor} {{')
        lines.append(f'    label="{actor}";')
        lines.append(f'    style=filled; fillcolor="{color}";')
        for n in nodes:
            text = n["text"].replace('"', "'")
            shape = {"Diamond": "diamond", "Oval": "ellipse"}.get(n["shape"], "box")
            lines.append(f'    {n["id"]} [label="{text}", shape={shape}];')
        lines.append("  }")

    for e in workflow["edges"]:
        label = f' [label="{e["label"]}"]' if e["label"] else ""
        lines.append(f'  {e["from"]} -> {e["to"]}{label};')

    lines.append("}")
    return {"format": "graphviz_dot", "definition": "\n".join(lines)}


def tool_render_html(workflow: dict, layout: dict | None = None) -> dict:
    """Generates a self-contained HTML file with an SVG swim-lane diagram."""
    NODE_W, NODE_H = 180, 50
    LANE_PAD       = 20
    H_GAP, V_GAP   = 40, 30
    LANE_HEADER    = 36

    actors  = list(dict.fromkeys(n["actor"] for n in workflow["nodes"]))
    n_lanes = len(actors)
    lane_h  = LANE_HEADER + NODE_H + V_GAP * 2

    in_degree: dict[str, int] = defaultdict(int)
    adjacency: dict[str, list[str]] = defaultdict(list)
    node_ids = [n["id"] for n in workflow["nodes"]]
    
    for nid in node_ids:
        in_degree.setdefault(nid, 0)
    
    for e in workflow["edges"]:
        adjacency[e["from"]].append(e["to"])
        in_degree[e["to"]] += 1

    queue = deque(nid for nid in node_ids if in_degree[nid] == 0)
    col_map: dict[str, int] = {}
    col = 0
    
    while queue:
        batch = list(queue)
        queue.clear()
        for nid in batch:
            col_map[nid] = col
            for nbr in adjacency[nid]:
                in_degree[nbr] -= 1
                if in_degree[nbr] == 0:
                    queue.append(nbr)
        col += 1

    n_cols  = max(col_map.values(), default=0) + 1
    SVG_W   = LANE_PAD * 2 + n_cols * (NODE_W + H_GAP)
    SVG_H   = LANE_PAD * 2 + n_lanes * lane_h

    node_map  = {n["id"]: n for n in workflow["nodes"]}
    actor_idx = {a: i for i, a in enumerate(actors)}

    def cx(nid: str) -> float:
        return LANE_PAD + col_map[nid] * (NODE_W + H_GAP) + NODE_W / 2

    def cy(nid: str) -> float:
        actor = node_map[nid]["actor"]
        lane  = actor_idx[actor]
        return LANE_PAD + lane * lane_h + LANE_HEADER + V_GAP + NODE_H / 2

    PALETTE = ["#e8f4fd", "#eaf7ec", "#fef9e7", "#fdecea", "#f0ebf8", "#e8f8fb", "#f5f5f5", "#fef3e2"]

    svg_parts: list[str] = []
    svg_parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_W}" height="{SVG_H}">')
    svg_parts.append(
        '<defs>'
        '<marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">'
        '<path d="M0,0 L0,6 L8,3 z" fill="#555"/></marker>'
        '</defs>'
    )

    for i, actor in enumerate(actors):
        y = LANE_PAD + i * lane_h
        color = PALETTE[i % len(PALETTE)]
        svg_parts.append(
            f'<rect x="{LANE_PAD}" y="{y}" width="{SVG_W - LANE_PAD*2}" height="{lane_h}" '
            f'fill="{color}" stroke="#ccc" stroke-width="1" rx="6"/>'
        )
        label = textwrap.shorten(actor, width=60, placeholder="…")
        svg_parts.append(
            f'<text x="{LANE_PAD + 8}" y="{y + 22}" '
            f'font-family="Segoe UI,Arial,sans-serif" font-size="11" font-weight="bold" fill="#333">'
            f'{label}</text>'
        )

    for e in workflow["edges"]:
        x1, y1 = cx(e["from"]), cy(e["from"])
        x2, y2 = cx(e["to"]),   cy(e["to"])
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        svg_parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="#555" stroke-width="1.5" marker-end="url(#arrow)"/>'
        )
        if e["label"]:
            svg_parts.append(
                f'<text x="{mx:.1f}" y="{my - 4:.1f}" '
                f'text-anchor="middle" font-family="Segoe UI,Arial,sans-serif" '
                f'font-size="10" fill="#c0392b" font-weight="bold">{e["label"]}</text>'
            )

    for n in workflow["nodes"]:
        nid   = n["id"]
        x, y  = cx(nid) - NODE_W / 2, cy(nid) - NODE_H / 2
        label = textwrap.shorten(n["text"], width=28, placeholder="…")

        if n["shape"] == "Diamond":
            hw, hh = NODE_W / 2, NODE_H / 2
            px, py = cx(nid), cy(nid)
            pts = (f"{px},{py - hh} {px + hw},{py} {px},{py + hh} {px - hw},{py}")
            svg_parts.append(
                f'<polygon points="{pts}" fill="#fffde7" stroke="#f39c12" stroke-width="2"/>'
            )
        elif n["shape"] == "Oval":
            svg_parts.append(
                f'<ellipse cx="{cx(nid):.1f}" cy="{cy(nid):.1f}" '
                f'rx="{NODE_W/2}" ry="{NODE_H/2}" '
                f'fill="#f0fff0" stroke="#27ae60" stroke-width="2"/>'
            )
        else:
            svg_parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{NODE_W}" height="{NODE_H}" '
                f'rx="6" fill="#fff" stroke="#2980b9" stroke-width="2"/>'
            )

        svg_parts.append(
            f'<text x="{cx(nid):.1f}" y="{cy(nid):.1f}" '
            f'text-anchor="middle" dominant-baseline="middle" '
            f'font-family="Segoe UI,Arial,sans-serif" font-size="10" fill="#222">'
            f'{label}</text>'
        )

    svg_parts.append("</svg>")
    svg_code = "\n".join(svg_parts)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8"/>
<title>Workflow Diagram</title>
<style>
  body {{ margin: 0; background: #f4f6f8; display: flex; flex-direction: column;
          align-items: center; padding: 24px; font-family: Segoe UI, Arial, sans-serif; }}
  h1   {{ color: #2c3e50; font-size: 1.4rem; margin-bottom: 16px; }}
  .wrap {{ background: #fff; border-radius: 12px; box-shadow: 0 4px 20px #0001;
           padding: 20px; overflow-x: auto; }}
</style>
</head>
<body>
<h1>Workflow — Processus de Marché</h1>
<div class="wrap">
{svg_code}
</div>
</body>
</html>"""

    return {"format": "html", "html": html}


# ---------------------------------------------------------------------------
# 3.  TOOL REGISTRY  (Cerebras format — FIXED)
# ---------------------------------------------------------------------------
# FIXED: Added proper 'properties' and 'required' to parameters schema.
# The Cerebras API requires 'properties' or 'anyOf' when strict=True.

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "validate_workflow",
            "description": "Validates the workflow JSON for structural integrity. Checks that all edge endpoints reference existing nodes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "object",
                        "description": "The workflow JSON to validate."
                    }
                },
                "required": ["workflow"],
                "additionalProperties": False,
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_layout",
            "description": "Analyzes the workflow topology and assigns swim-lane indices to actors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "object",
                        "description": "The workflow JSON to analyze."
                    }
                },
                "required": ["workflow"],
                "additionalProperties": False,
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "render_mermaid",
            "description": "Generates a Mermaid flowchart definition from the workflow.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "object",
                        "description": "The workflow JSON."
                    }
                },
                "required": ["workflow"],
                "additionalProperties": False,
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "render_graphviz",
            "description": "Generates a Graphviz DOT definition from the workflow.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "object",
                        "description": "The workflow JSON."
                    }
                },
                "required": ["workflow"],
                "additionalProperties": False,
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "render_html",
            "description": "Generates a self-contained HTML file with an SVG swim-lane diagram.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "object",
                        "description": "The workflow JSON."
                    }
                },
                "required": ["workflow"],
                "additionalProperties": False,
            },
        }
    },
]


def dispatch_tool(name: str, arguments: str) -> Any:
    """Routes a tool call to the correct implementation.
    
    Arguments come as JSON strings from the API, as per Cerebras docs.
    """
    handlers = {
        "validate_workflow": tool_validate_workflow,
        "analyze_layout":    tool_analyze_layout,
        "render_mermaid":    tool_render_mermaid,
        "render_graphviz":   tool_render_graphviz,
        "render_html":       tool_render_html,
    }
    
    if name not in handlers:
        return {"error": f"Unknown tool: {name}"}
    
    # Parse JSON arguments - they come as a JSON string from the API
    try:
        args_dict = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (json.JSONDecodeError, TypeError) as e:
        return {"error": f"Failed to parse arguments: {e}"}
    
    try:
        result = handlers[name](**args_dict)
        return result
    except Exception as e:
        return {"error": f"Tool execution failed: {e}"}


# ---------------------------------------------------------------------------
# 4.  AGENT LOOP  (Following Cerebras multi-turn pattern)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a Workflow Diagram Generation Agent.

Your job is to:
1. Call validate_workflow to check the workflow integrity
2. Call analyze_layout to understand the structure
3. Call render_mermaid to create a Mermaid diagram
4. Call render_graphviz to create a Graphviz diagram
5. Call render_html to create an HTML/SVG diagram
6. Provide a brief summary

Execute the tools in this exact order. Pass the full workflow object to each tool.
"""


@dataclass
class AgentResult:
    mermaid_def:  str = ""
    graphviz_def: str = ""
    html_content: str = ""
    summary:      str = ""
    tool_calls:   list[str] = field(default_factory=list)


def run_agent(workflow: dict, verbose: bool = True) -> AgentResult:
    """Drives the agentic loop using Cerebras multi-turn pattern."""
    api_key = os.environ.get("CEREBRAS_API_KEY")
    if not api_key:
        raise EnvironmentError("Set the CEREBRAS_API_KEY environment variable before running.")

    client = Cerebras(api_key=api_key)
    # FIXED: Use a valid model name. Options include:
    #   "llama3.1-8b", "llama-3.3-70b", "llama-4-scout-17b-16e-instruct"
    MODEL = "qwen-3-235b-a22b-instruct-2507"

    # Initialize messages following Cerebras docs pattern
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        },
        {
            "role": "user",
            "content": f"Generate all diagram formats for this workflow:\n\n{json.dumps(workflow, ensure_ascii=False, indent=2)}"
        }
    ]

    result = AgentResult()
    max_iterations = 10
    iteration = 0

    # Multi-turn loop following Cerebras docs
    while iteration < max_iterations:
        iteration += 1

        if verbose:
            print(f"\n[agent] iteration={iteration}, messages={len(messages)}")

        # Make API call with tools
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            parallel_tool_calls=False,
        )

        # Get the message from response
        choice = response.choices[0]
        message = choice.message

        if verbose:
            print(f"  finish_reason: {choice.finish_reason}")

        # Add assistant's response to messages (per Cerebras docs)
        messages.append(message.model_dump())

        # Collect text content
        if message.content:
            result.summary += message.content + "\n"
            if verbose:
                print(f"  assistant: {message.content[:100]}...")

        # Check if there are tool calls
        if not message.tool_calls:
            if verbose:
                print("  → no tool calls, finishing")
            break

        # Execute tool calls (per Cerebras multi-turn pattern)
        for tool_call in message.tool_calls:
            function_call = tool_call.function
            tool_name = function_call.name
            
            result.tool_calls.append(tool_name)

            if verbose:
                print(f"  → tool: {tool_name}")

            # CRITICAL: Pass arguments as-is (already JSON string from API)
            tool_output = dispatch_tool(tool_name, function_call.arguments)

            # Stash rendered artifacts
            if tool_name == "render_mermaid":
                result.mermaid_def = tool_output.get("definition", "")
            elif tool_name == "render_graphviz":
                result.graphviz_def = tool_output.get("definition", "")
            elif tool_name == "render_html":
                result.html_content = tool_output.get("html", "")

            # Append tool result to messages (per Cerebras docs)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(tool_output)
            })

            if verbose and "error" in tool_output:
                print(f"     error: {tool_output['error']}")

    return result


# ---------------------------------------------------------------------------
# 5.  OUTPUT WRITER
# ---------------------------------------------------------------------------

def save_outputs(result: AgentResult, output_dir: str = "diagram_output") -> dict[str, Path]:
    """Saves all generated diagram formats to disk."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}

    if result.mermaid_def:
        p = out / "workflow.mmd"
        p.write_text(result.mermaid_def, encoding="utf-8")
        paths["mermaid"] = p

    if result.graphviz_def:
        p = out / "workflow.dot"
        p.write_text(result.graphviz_def, encoding="utf-8")
        paths["graphviz"] = p

    if result.html_content:
        p = out / "workflow.html"
        p.write_text(result.html_content, encoding="utf-8")
        paths["html"] = p

    return paths


# ---------------------------------------------------------------------------
# 6.  ENTRY POINT
# ---------------------------------------------------------------------------

def main(workflow= WORKFLOW) -> None:
    api_key = os.environ.get("CEREBRAS_API_KEY")
    if not api_key:
        raise EnvironmentError("Set the CEREBRAS_API_KEY environment variable before running.")

    print("=" * 60)
    print("  Workflow Diagram Generation Agent  (powered by Cerebras)")
    print("=" * 60)

    result = run_agent(workflow, verbose=True)

    paths = save_outputs(result)

    print("\n" + "=" * 60)
    print("  Agent Summary")
    print("=" * 60)
    print(result.summary.strip())

    print("\n  Tools called:", " → ".join(result.tool_calls))
    print("\n  Output files:")
    for fmt, path in paths.items():
        print(f"    [{fmt:10s}]  {path}")

    print("\n  Open workflow.html in your browser to view the diagram.")


if __name__ == "__main__":
    main()