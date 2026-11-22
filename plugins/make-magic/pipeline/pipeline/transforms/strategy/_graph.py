"""Tiny, dependency-free graph ops for the STRUCTURE-ONLY engine facts.

The validated prototype used ``networkx`` for two things — weakly-connected
components (the convergent event engines) and greedy-modularity communities
(the structure communities). Both are reported as *facts* under
``facts["engines"]`` and, per the engine's own contract, **never decide the
label**. networkx is not a pipeline dependency, so this module re-implements
exactly those two operations in pure Python with deterministic ordering.

``greedy_modularity_communities`` is the standard Clauset-Newman-Moore greedy
agglomeration (merge the community pair with the largest modularity gain until
no positive gain remains), with ties broken on sorted node labels so the output
is byte-stable across runs and hash seeds.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Iterable


def weakly_connected_components[T: Hashable](
    edges: Iterable[tuple[T, T]],
) -> list[set[T]]:
    """Weakly-connected components of a directed edge list (direction ignored).

    Returns the components as a list of node sets. Only nodes that appear in an
    edge are included (matching ``networkx.weakly_connected_components`` over a
    ``DiGraph`` built solely from ``add_edge``).
    """
    adj: dict[T, set[T]] = defaultdict(set)
    nodes: set[T] = set()
    for u, v in edges:
        adj[u].add(v)
        adj[v].add(u)
        nodes.add(u)
        nodes.add(v)
    seen: set[T] = set()
    comps: list[set[T]] = []
    for start in nodes:
        if start in seen:
            continue
        comp: set[T] = set()
        stack = [start]
        seen.add(start)
        while stack:
            n = stack.pop()
            comp.add(n)
            for m in adj[n]:
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        comps.append(comp)
    return comps


def strongly_connected_components[T: Hashable](
    nodes: Iterable[T],
    edges: Iterable[tuple[T, T]],
) -> list[list[T]]:
    """Tarjan strongly-connected components of a directed graph (deterministic).

    The unified event-graph (ADR 0026) models CardAttribute | Event nodes; a
    *loop/engine* is a directed cycle, which is exactly a strongly-connected
    component of size >= 2 (or a 1-node SCC with a self-edge). This is the single
    SCC pass that replaces the ``producers & consumers`` recursive-engine
    heuristic: an event is ``recursive-engine`` iff its node lands in a
    nontrivial SCC.

    Determinism is load-bearing (ADR 0003 byte-parity). Tarjan's output order
    depends on DFS start order and neighbour iteration order, so both are
    **sorted** (by ``str(node)``): nodes are visited in sorted order and each
    node's successors are pushed in reverse-sorted order (so they pop in sorted
    order). Within one SCC the member list is returned sorted; the list of SCCs
    is returned sorted by each SCC's sorted member tuple. The implementation is
    **iterative** (explicit stack) so a deep corpus graph cannot blow the
    recursion limit.

    Parameters
    ----------
    nodes
        Every node to consider (isolated nodes included — each forms its own
        singleton SCC, matching the convention that a node with no cycle is
        trivially its own component).
    edges
        Directed ``(u, v)`` pairs. An edge whose endpoint is absent from *nodes*
        is ignored (the node set is authoritative).

    Returns
    -------
    list[list[T]]
        The SCCs, each a sorted node list; the outer list sorted by member tuple.
    """
    node_set: set[T] = set(nodes)
    adj: dict[T, list[T]] = {n: [] for n in node_set}
    for u, v in edges:
        if u in node_set and v in node_set:
            adj[u].append(v)
    # Neighbours sorted; pushing in reverse means successors are explored in
    # ascending order — a stable, hash-seed-independent traversal.
    succ: dict[T, list[T]] = {n: sorted(set(adj[n]), key=_keyfn, reverse=True) for n in node_set}

    index_of: dict[T, int] = {}
    lowlink: dict[T, int] = {}
    on_stack: set[T] = set()
    scc_stack: list[T] = []
    counter = 0
    sccs: list[list[T]] = []

    for root in sorted(node_set, key=_keyfn):
        if root in index_of:
            continue
        # work stack holds (node, neighbour-cursor) frames.
        work: list[list] = [[root, 0]]
        index_of[root] = lowlink[root] = counter
        counter += 1
        on_stack.add(root)
        scc_stack.append(root)
        while work:
            frame = work[-1]
            u, i = frame[0], frame[1]
            neighbours = succ[u]
            if i < len(neighbours):
                frame[1] += 1
                w = neighbours[i]
                if w not in index_of:
                    index_of[w] = lowlink[w] = counter
                    counter += 1
                    on_stack.add(w)
                    scc_stack.append(w)
                    work.append([w, 0])
                elif w in on_stack:
                    lowlink[u] = min(lowlink[u], index_of[w])
            else:
                # u is fully explored; if it is an SCC root, pop its component.
                if lowlink[u] == index_of[u]:
                    comp: list[T] = []
                    while True:
                        w = scc_stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == u:
                            break
                    sccs.append(sorted(comp, key=_keyfn))
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[u])

    sccs.sort(key=lambda comp: [_keyfn(x) for x in comp])
    return sccs


def greedy_modularity_communities[T: Hashable](
    nodes: Iterable[T],
    weighted_edges: Iterable[tuple[T, T, float]],
) -> list[list[T]]:
    """Clauset-Newman-Moore greedy modularity communities (deterministic).

    ``weighted_edges`` are undirected ``(u, v, weight)`` triples. Isolated nodes
    (no edges) each form their own singleton community, matching networkx. The
    result is a list of communities, each a sorted node list; communities are
    ordered largest-weight-first then by their sorted members.
    """
    node_list = sorted(nodes, key=_keyfn)
    if not node_list:
        return []
    # Collapse parallel edges by summing weights; drop self loops.
    w: dict[frozenset[T], float] = defaultdict(float)
    deg: dict[T, float] = defaultdict(float)
    m2 = 0.0  # 2m = total weight * 2 (each edge counted once here -> m)
    present = set(node_list)
    for u, v, wt in weighted_edges:
        if u == v or u not in present or v not in present:
            continue
        w[frozenset((u, v))] += wt
        deg[u] += wt
        deg[v] += wt
        m2 += wt
    m = m2
    if m <= 0:
        # no edges -> every node is its own community
        return [[n] for n in node_list]

    # community -> set of member nodes; start singletons
    comm: dict[T, set[T]] = {n: {n} for n in node_list}
    comm_deg: dict[T, float] = {n: deg[n] for n in node_list}
    # adjacency weight between communities
    adjw: dict[T, dict[T, float]] = defaultdict(dict)
    for pair, wt in w.items():
        u, v = tuple(pair)  # self-loops already dropped, so every pair has 2 members
        adjw[u][v] = adjw[u].get(v, 0.0) + wt
        adjw[v][u] = adjw[v].get(u, 0.0) + wt

    def gain(a: T, b: T) -> float:
        # ΔQ for merging communities a,b = eab/m - (deg_a*deg_b)/(2 m^2)
        eab = adjw.get(a, {}).get(b, 0.0)
        return eab / m - (comm_deg[a] * comm_deg[b]) / (2.0 * m * m)

    alive = set(node_list)
    while True:
        best: tuple[float, T, T] | None = None
        for a in sorted(alive, key=_keyfn):
            for b in sorted(adjw.get(a, {}), key=_keyfn):
                if b not in alive or _keyfn(b) <= _keyfn(a):
                    continue  # each unordered pair considered once
                g = gain(a, b)
                if g <= 0:
                    continue
                cand = (g, a, b)
                if best is None or _better(cand, best):
                    best = cand
        if best is None:
            break
        _g, a, b = best
        # merge b into a (keep the lexicographically smaller label as the rep)
        keep, drop = (a, b) if _keyfn(a) <= _keyfn(b) else (b, a)
        comm[keep] |= comm[drop]
        comm_deg[keep] += comm_deg[drop]
        # merge adjacency
        for nb, wt in list(adjw.get(drop, {}).items()):
            if nb == keep:
                continue
            adjw[keep][nb] = adjw[keep].get(nb, 0.0) + wt
            adjw[nb][keep] = adjw[nb].get(keep, 0.0) + wt
            adjw[nb].pop(drop, None)
        adjw[keep].pop(drop, None)
        adjw.pop(drop, None)
        alive.discard(drop)
        comm.pop(drop, None)
        comm_deg.pop(drop, None)

    communities = [sorted(members, key=_keyfn) for members in comm.values()]
    communities.sort(key=lambda members: (-len(members), [_keyfn(x) for x in members]))
    return communities


def _keyfn(x: Hashable) -> str:
    return str(x)


def _better(cand: tuple[float, Hashable, Hashable], best: tuple[float, Hashable, Hashable]) -> bool:
    # higher gain wins; ties broken deterministically on the (a,b) labels
    if cand[0] != best[0]:
        return cand[0] > best[0]
    return (_keyfn(cand[1]), _keyfn(cand[2])) < (_keyfn(best[1]), _keyfn(best[2]))
