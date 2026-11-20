"""Unified event graph — events-as-nodes, true-SCC recursion, loop-aware admission.

Covers the Phase-1 framework of ADR 0026 (issue #83):

* ``strongly_connected_components`` is a deterministic Tarjan pass.
* ``unified_event_graph_edges`` restores the ``attr → event → attr`` bipartite
  legs (events are first-class NODES) and interprets a ``RelayEntry`` as a GATED
  event→event edge — never dropping it.
* ``recursive_event_set`` flags an event recursive iff it sits on a true cycle
  (a nontrivial SCC), replacing the ``producers & consumers`` heuristic: a
  straight chain A→B→C is NOT recursive; a real cycle IS.
* ``loop_aware_nodes`` admits a sub-threshold attr only when it is structurally
  load-bearing on a closed cycle anchored by an otherwise-substantive node.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from pipeline.transforms.strategy._graph import strongly_connected_components
from pipeline.transforms.strategy.assets import StrategyAssets
from pipeline.transforms.strategy.derive_card_events import RelayEntry
from pipeline.transforms.strategy.strategy_graph import (
    loop_aware_nodes,
    recursive_event_set,
    unified_event_graph_edges,
)

# ---------------------------------------------------------------------------
# Tarjan SCC
# ---------------------------------------------------------------------------


class TestStronglyConnectedComponents:
    def test_chain_is_all_singletons(self) -> None:
        sccs = strongly_connected_components(['a', 'b', 'c'], [('a', 'b'), ('b', 'c')])
        assert sccs == [['a'], ['b'], ['c']]

    def test_cycle_is_one_component(self) -> None:
        sccs = strongly_connected_components(['a', 'b', 'c'], [('a', 'b'), ('b', 'c'), ('c', 'a')])
        assert sccs == [['a', 'b', 'c']]

    def test_mixed_cycle_and_tail(self) -> None:
        sccs = strongly_connected_components(['a', 'b', 'c', 'd'], [('a', 'b'), ('b', 'a'), ('b', 'c'), ('c', 'd')])
        assert sccs == [['a', 'b'], ['c'], ['d']]

    def test_deterministic_across_input_order(self) -> None:
        edges = [('a', 'b'), ('b', 'c'), ('c', 'a'), ('c', 'd'), ('d', 'e'), ('e', 'c')]
        r1 = strongly_connected_components(['a', 'b', 'c', 'd', 'e'], edges)
        r2 = strongly_connected_components(['e', 'd', 'c', 'b', 'a'], list(reversed(edges)))
        assert r1 == r2 == [['a', 'b', 'c', 'd', 'e']]

    def test_edge_to_absent_node_ignored(self) -> None:
        # ('b','z') has an endpoint outside the node set and must be dropped.
        sccs = strongly_connected_components(['a', 'b'], [('a', 'b'), ('b', 'z')])
        assert sccs == [['a'], ['b']]


# ---------------------------------------------------------------------------
# unified_event_graph_edges — events-as-nodes + relay interpretation
# ---------------------------------------------------------------------------


class TestUnifiedEventGraphEdges:
    def test_bipartite_legs_restored(self) -> None:
        # p produces E (p→E), E triggers c (E→c): the event is a NODE between them.
        events = {'E': {'produced_by': ['p'], 'triggers': ['c']}}
        _gn, edges = unified_event_graph_edges([], events, {'p', 'c'})
        assert (('a', 'p'), ('e', 'E')) in edges
        assert (('e', 'E'), ('a', 'c')) in edges

    def test_relay_forms_gated_event_edge_when_gate_present(self) -> None:
        events = {
            'E1': {'produced_by': ['a'], 'triggers': ['q']},
            'E2': {'produced_by': [RelayEntry('E1', 'g')], 'triggers': ['a']},
        }
        _gn, edges = unified_event_graph_edges([], events, {'a', 'q', 'g'})
        assert (('e', 'E1'), ('e', 'E2')) in edges

    def test_relay_edge_absent_when_gate_not_a_node(self) -> None:
        events = {
            'E1': {'produced_by': ['a'], 'triggers': ['q']},
            'E2': {'produced_by': [RelayEntry('E1', 'g')], 'triggers': ['a']},
        }
        _gn, edges = unified_event_graph_edges([], events, {'a', 'q'})  # no 'g'
        assert (('e', 'E1'), ('e', 'E2')) not in edges

    def test_augmented_event_edges_contribute_legs(self) -> None:
        # An attr injected only via event_edges (not in the catalog dict) still
        # appears — the relay-augmented walk relies on this.
        _gn, edges = unified_event_graph_edges([('x', 'y', 'Einj')], {}, set())
        assert (('a', 'x'), ('e', 'Einj')) in edges
        assert (('e', 'Einj'), ('a', 'y')) in edges


# ---------------------------------------------------------------------------
# recursive_event_set — true-SCC recursive-engine
# ---------------------------------------------------------------------------


class TestRecursiveEventSet:
    def test_straight_chain_is_not_recursive(self) -> None:
        events = {
            'E1': {'produced_by': ['p'], 'triggers': ['q']},
            'E2': {'produced_by': ['q'], 'triggers': ['r']},
        }
        assert recursive_event_set([], events, {'p', 'q', 'r'}) == set()

    def test_true_cycle_is_recursive(self) -> None:
        events = {
            'E1': {'produced_by': ['p'], 'triggers': ['q']},
            'E2': {'produced_by': ['q'], 'triggers': ['p']},
        }
        assert recursive_event_set([], events, {'p', 'q'}) == {'E1', 'E2'}

    def test_self_pair_event_is_recursive(self) -> None:
        # An attr on BOTH sides of one event is a genuine attr→ev→attr 2-cycle.
        events = {'E': {'produced_by': ['x'], 'triggers': ['x']}}
        assert recursive_event_set([], events, {'x'}) == {'E'}

    def test_synthetic_relay_closes_cycle_only_when_gated(self) -> None:
        events = {
            'E1': {'produced_by': ['a'], 'triggers': ['q']},
            'E2': {'produced_by': [RelayEntry('E1', 'g')], 'triggers': ['a']},
        }
        assert recursive_event_set([], events, {'a', 'q', 'g'}) == {'E1', 'E2'}
        assert recursive_event_set([], events, {'a', 'q'}) == set()


# ---------------------------------------------------------------------------
# loop_aware_nodes — loop-aware substantiveness admission
# ---------------------------------------------------------------------------


class TestLoopAwareNodes:
    def _assets(self, events: dict) -> StrategyAssets:
        # loop_aware_nodes reads only ``.events``; a duck-typed shim is enough at
        # runtime, cast to the real type so the call sites type-check.
        return cast(StrategyAssets, SimpleNamespace(events=events))

    def test_subthreshold_on_cycle_is_admitted(self) -> None:
        # p (prevalent) ↔ a (sub-threshold) via Ex/Ey; a is load-bearing on the loop.
        events = {
            'Ex': {'produced_by': ['p'], 'triggers': ['a']},
            'Ey': {'produced_by': ['a'], 'triggers': ['p']},
        }
        aw = {'p': 5.0, 'a': 1.0, 'z': 1.0}
        admitted = loop_aware_nodes({'p'}, aw, self._assets(events))
        assert 'a' in admitted
        assert 'z' not in admitted  # off-cycle sub-threshold stays out

    def test_noise_only_cycle_not_admitted(self) -> None:
        # Two sub-threshold attrs form a cycle with NO prevalent anchor → not admitted.
        events = {
            'Ex': {'produced_by': ['a'], 'triggers': ['b']},
            'Ey': {'produced_by': ['b'], 'triggers': ['a']},
        }
        aw = {'p': 5.0, 'a': 1.0, 'b': 1.0}
        admitted = loop_aware_nodes({'p'}, aw, self._assets(events))
        assert admitted == {'p'}

    def test_no_candidates_returns_prevalent(self) -> None:
        events = {'Ex': {'produced_by': ['p'], 'triggers': ['q']}}
        aw = {'p': 5.0, 'q': 5.0}
        assert loop_aware_nodes({'p', 'q'}, aw, self._assets(events)) == {'p', 'q'}
