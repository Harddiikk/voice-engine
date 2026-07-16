"""Duplicate edge labels within a node must not produce duplicate function
names — Gemini Live rejects duplicate declarations with websocket 1011."""

from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph


def _wf(edges):
    nodes = [
        {"id": "1", "type": "startCall", "position": {"x": 0, "y": 0},
         "data": {"name": "Start", "prompt": "p", "is_start": True}},
        {"id": "2", "type": "agentNode", "position": {"x": 0, "y": 0},
         "data": {"name": "A", "prompt": "p"}},
        {"id": "3", "type": "agentNode", "position": {"x": 0, "y": 0},
         "data": {"name": "B", "prompt": "p"}},
        {"id": "7", "type": "endCall", "position": {"x": 0, "y": 0},
         "data": {"name": "End", "prompt": "p", "is_end": True}},
    ]
    return ReactFlowDTO(nodes=nodes, edges=edges)


def _edge(src, tgt, label):
    return {"id": f"e{src}-{tgt}", "source": src, "target": tgt,
            "data": {"label": label, "condition": "c"}}


def test_duplicate_labels_get_unique_function_names():
    graph = WorkflowGraph(_wf([
        _edge("1", "2", "Move to probe"),
        _edge("1", "3", "Move to probe"),
        _edge("1", "7", "End call"),
    ]))
    names = [e.get_function_name() for e in graph.nodes["1"].out_edges]
    assert len(set(names)) == 3, names
    assert "move_to_probe" in names
    assert "move_to_probe_to_3" in names


def test_unique_labels_keep_plain_names():
    graph = WorkflowGraph(_wf([
        _edge("1", "2", "Move to probe"),
        _edge("2", "3", "Recommend"),
        _edge("2", "7", "End call"),
    ]))
    assert graph.nodes["1"].out_edges[0].get_function_name() == "move_to_probe"
    names2 = [e.get_function_name() for e in graph.nodes["2"].out_edges]
    assert names2 == ["recommend", "end_call"]
