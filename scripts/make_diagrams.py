"""Generate expansion and scoring diagrams for the shallow search algorithm."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
import numpy as np

# ── Shared layout constants ────────────────────────────────────────────────────
FIG_W, FIG_H = 20, 11
XLIM = (0, 20)
YLIM = (0, 11)
R = 0.52   # outer node radius (data units)

C_MOVE1  = '#2563EB'
C_MOVE2  = '#EA580C'
C_SWITCH = '#16A34A'
ACTION_COLORS = [C_MOVE1, C_MOVE2, C_SWITCH]
ACTION_NAMES  = ['move 1', 'move 2', 'switch']

ROOT = (10.0, 10.2)
T1   = [(3.1, 7.8), (10.0, 7.8), (16.9, 7.8)]
T2   = [
    (1.5, 5.2), (4.7, 5.2),
    (8.4, 5.2), (11.6, 5.2),
    (15.3, 5.2), (18.5, 5.2),
]
LEAVES = [
    (0.7, 2.6),  (2.3, 2.6),
    (3.9, 2.6),  (5.5, 2.6),
    (7.6, 2.6),  (9.2, 2.6),
    (10.8, 2.6), (12.4, 2.6),
    (14.5, 2.6), (16.1, 2.6),
    (17.7, 2.6), (19.3, 2.6),
]

# Inner-dot offsets (relative to node center, in data units)
DOT_OFFSETS = [
    (-0.20, 0.17), (0.0, 0.22), (0.20, 0.17),
    (-0.18, -0.13), (0.18, -0.13),
]
DOT_R = 0.065


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _shorten(p1, p2):
    x1, y1 = p1;  x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    dist = (dx**2 + dy**2) ** 0.5
    if dist < 2 * R:
        return p1, p2
    nx, ny = dx / dist, dy / dist
    return (x1 + nx * R, y1 + ny * R), (x2 - nx * R, y2 - ny * R)


def _arrow(ax, p1, p2, color='#374151', lw=1.6, scale=11):
    start, end = _shorten(p1, p2)
    ax.annotate('', xy=end, xytext=start,
                arrowprops=dict(arrowstyle='->', color=color,
                                lw=lw, mutation_scale=scale),
                zorder=3)


def _node(ax, pos, label, fc, ec, dot_color, fontsize=8.5, bold=True):
    """Draw a circle with inner dots representing multiple sampled states."""
    cx, cy = pos
    ax.add_patch(plt.Circle(pos, R, fc=fc, ec=ec, lw=1.8, zorder=5))
    for dx, dy in DOT_OFFSETS:
        ax.add_patch(plt.Circle((cx + dx, cy + dy), DOT_R,
                                fc=dot_color, ec='none', alpha=0.60, zorder=6))
    ax.text(cx, cy, label, ha='center', va='center',
            fontsize=fontsize, fontweight='bold' if bold else 'normal',
            color='black', zorder=7,
            bbox=dict(fc=fc, alpha=0.80, ec='none', pad=1.2))


def _level_label(ax, y, top_line, bottom_line=''):
    ax.text(-0.3, y, top_line, fontsize=9, va='center', ha='right',
            color='#374151', fontweight='bold')
    if bottom_line:
        ax.text(-0.3, y - 0.42, bottom_line, fontsize=8, va='center', ha='right',
                color='#6B7280')


def _badge(ax, x, y, text, fc, ec, fontsize=7.5):
    ax.text(x, y, text, ha='center', va='center', fontsize=fontsize,
            color=ec, style='italic',
            bbox=dict(fc=fc, ec=ec, lw=0.8, pad=2.5, boxstyle='round,pad=0.3'))


# ── Diagram 1: Expansion ───────────────────────────────────────────────────────

def make_expansion():
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
    ax.set_xlim(*XLIM);  ax.set_ylim(*YLIM)
    ax.axis('off');      fig.patch.set_facecolor('white')
    ax.set_facecolor('white')

    _level_label(ax, 10.2, 'Turn 0', '(Root)')
    _level_label(ax, 7.8,  'Turn 1', 'States')
    _level_label(ax, 5.2,  'Turn 2', 'States')
    _level_label(ax, 2.6,  'Turn 3', '(Leaves)')

    # Root → T1: one colored arrow per action
    for i, (color, name) in enumerate(zip(ACTION_COLORS, ACTION_NAMES)):
        _arrow(ax, ROOT, T1[i], color=color, lw=2.2, scale=13)

    _badge(ax, 6.0,  9.35, 'move 1  ×30 samples', '#EFF6FF', C_MOVE1)
    _badge(ax, 10.0, 9.78, 'move 2  ×30 samples', '#FFF7ED', C_MOVE2)
    _badge(ax, 14.0, 9.35, 'switch  ×30 samples', '#F0FDF4', C_SWITCH)

    # T1 → T2: gray arrows (each T1 fans to 2 T2 children)
    for i, t1_pos in enumerate(T1):
        ta, tb = T2[i * 2], T2[i * 2 + 1]
        _arrow(ax, t1_pos, ta, color='#6B7280', lw=1.5)
        _arrow(ax, t1_pos, tb, color='#6B7280', lw=1.5)

    _badge(ax, 10.0, 6.55, '×30 samples per action  (all Turn 1 nodes expanded)',
           '#F9FAFB', '#6B7280', fontsize=8)

    # T2 → Leaves: gray arrows
    for i, t2_pos in enumerate(T2):
        la, lb = LEAVES[i * 2], LEAVES[i * 2 + 1]
        _arrow(ax, t2_pos, la, color='#6B7280', lw=1.5)
        _arrow(ax, t2_pos, lb, color='#6B7280', lw=1.5)

    _badge(ax, 10.0, 3.95, '×30 samples per action  (all Turn 2 nodes expanded)',
           '#F9FAFB', '#6B7280', fontsize=8)

    # Nodes
    _node(ax, ROOT, 'ROOT', fc='#FEF3C7', ec='#B45309', dot_color='#92400E', fontsize=10)

    for i, pos in enumerate(T1):
        _node(ax, pos, f'S{i+1}', fc='#DBEAFE', ec='#1D4ED8', dot_color='#1E3A8A')

    for i, pos in enumerate(T2):
        _node(ax, pos, f'S{i+4}', fc='#E0E7FF', ec='#6B7280', dot_color='#374151',
              fontsize=8)

    for i, pos in enumerate(LEAVES):
        _node(ax, pos, f'L{i+1}', fc='#D1FAE5', ec='#059669', dot_color='#065F46',
              fontsize=7.5, bold=False)

    legend_handles = [
        Line2D([0], [0], color=c, lw=2.2, label=n)
        for c, n in zip(ACTION_COLORS, ACTION_NAMES)
    ] + [mpatches.Patch(fc='#D1FAE5', ec='#059669', label='Leaf (scored at sim time)')]
    ax.legend(handles=legend_handles, loc='upper right', fontsize=9,
              framealpha=0.95, title='Legend', title_fontsize=9)

    ax.set_title('Diagram 1 — Search Expansion  (MAX_DEPTH = 3)',
                 fontsize=13, fontweight='bold', pad=14)

    plt.tight_layout()
    out = '/home/Fracture/PycharmProjects/NuzlockeSolver/diagram_expansion.png'
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f'Saved {out}')


# ── Diagram 2: Score Propagation ───────────────────────────────────────────────

LEAF_SCORES = [2.1, -0.3,  1.8,  0.5,  -1.2,  2.4,
               0.9,  1.6,  -0.7,  3.1,   1.3,  0.2]


def _weighted_avg(scores, weights):
    total = sum(weights)
    return sum(s * w for s, w in zip(scores, weights)) / total if total else sum(scores) / len(scores)


def make_scoring():
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
    ax.set_xlim(*XLIM);  ax.set_ylim(*YLIM)
    ax.axis('off');      fig.patch.set_facecolor('white')
    ax.set_facecolor('white')

    _level_label(ax, 10.2, 'Turn 0', '(Root)')
    _level_label(ax, 7.8,  'Turn 1', 'States')
    _level_label(ax, 5.2,  'Turn 2', 'States')
    _level_label(ax, 2.6,  'Turn 3', '(Leaves)')

    # ── Leaf nodes ──
    for pos, score in zip(LEAVES, LEAF_SCORES):
        _node(ax, pos, f'{score:+.1f}', fc='#D1FAE5', ec='#059669',
              dot_color='#065F46', fontsize=8.5)

    # ── Leaves → T2 (upward arrows) ──
    for i, t2_pos in enumerate(T2):
        la, lb = LEAVES[i * 2], LEAVES[i * 2 + 1]
        _arrow(ax, la, t2_pos, color='#059669', lw=1.4, scale=10)
        _arrow(ax, lb, t2_pos, color='#059669', lw=1.4, scale=10)
        # Weight label on the left branch
        s, e = _shorten(la, t2_pos)
        ax.text((s[0]+e[0])/2 + 0.18, (s[1]+e[1])/2, 'w=0.88',
                fontsize=6.5, color='#374151', ha='left', va='center', zorder=8)

    # Formula badge — lowered to avoid overlap
    _badge(ax, 10.0, 3.45,
           'score(action) = Σ wᵢ · scoreᵢ / Σ wᵢ     node score = max(action scores)',
           '#F0FDF4', '#059669', fontsize=8)

    # ── Compute T2 scores ──
    T2_SCORES = []
    T2_BEST   = []
    for i in range(len(T2)):
        sa, sb = LEAF_SCORES[i * 2], LEAF_SCORES[i * 2 + 1]
        scores = {
            'move 1': _weighted_avg([sa, sb], [0.88, 0.12]),
            'move 2': _weighted_avg([sa, sb], [0.55, 0.45]),
            'switch': _weighted_avg([sa, sb], [0.20, 0.80]),
        }
        best = max(scores, key=scores.__getitem__)
        T2_SCORES.append(round(scores[best], 2))
        T2_BEST.append(best)

    # ── T2 nodes ──
    for pos, score, best in zip(T2, T2_SCORES, T2_BEST):
        _node(ax, pos, f'{score:+.2f}', fc='#EDE9FE', ec='#7C3AED',
              dot_color='#5B21B6', fontsize=8.5)
        ax.text(pos[0], pos[1] - R - 0.22, f'↑ {best}',
                ha='center', va='top', fontsize=6.5, color='#5B21B6', zorder=8)

    # ── T2 → T1 (upward arrows) ──
    for i, t1_pos in enumerate(T1):
        ta, tb = T2[i * 2], T2[i * 2 + 1]
        _arrow(ax, ta, t1_pos, color='#7C3AED', lw=1.4, scale=10)
        _arrow(ax, tb, t1_pos, color='#7C3AED', lw=1.4, scale=10)

    _badge(ax, 10.0, 6.55,
           'Weighted avg per action  →  max selects best action  →  node score',
           '#EDE9FE', '#7C3AED', fontsize=8)

    # ── Compute T1 scores ──
    T1_SCORES = []
    T1_BEST   = []
    for i in range(len(T1)):
        sa, sb = T2_SCORES[i * 2], T2_SCORES[i * 2 + 1]
        scores = {
            'move 1': _weighted_avg([sa, sb], [0.85, 0.15]),
            'move 2': _weighted_avg([sa, sb], [0.50, 0.50]),
            'switch': _weighted_avg([sa, sb], [0.25, 0.75]),
        }
        best = max(scores, key=scores.__getitem__)
        T1_SCORES.append(round(scores[best], 2))
        T1_BEST.append(best)

    # ── T1 nodes ──
    for pos, score, best in zip(T1, T1_SCORES, T1_BEST):
        _node(ax, pos, f'{score:+.2f}', fc='#DBEAFE', ec='#1D4ED8',
              dot_color='#1E3A8A', fontsize=8.5)
        ax.text(pos[0], pos[1] - R - 0.22, f'↑ {best}',
                ha='center', va='top', fontsize=6.5, color='#1E40AF', zorder=8)

    # ── T1 → Root (colored arrows, one per action) ──
    for i, (t1_pos, color) in enumerate(zip(T1, ACTION_COLORS)):
        _arrow(ax, t1_pos, ROOT, color=color, lw=2.2, scale=13)

    # ── Root node ──
    root_scores = {name: T1_SCORES[i] for i, name in enumerate(ACTION_NAMES)}
    best_action = max(root_scores, key=root_scores.__getitem__)
    best_score  = root_scores[best_action]

    _node(ax, ROOT, f'{best_score:+.2f}', fc='#FEF3C7', ec='#B45309',
          dot_color='#92400E', fontsize=10)

    # Action score labels: horizontal row just above root, one per action color
    label_y = ROOT[1] + R + 0.20
    label_xs = [ROOT[0] - 3.2, ROOT[0], ROOT[0] + 3.2]
    for (name, color), lx in zip(zip(ACTION_NAMES, ACTION_COLORS), label_xs):
        sc     = root_scores[name]
        marker = ' ← BEST' if name == best_action else ''
        ax.text(lx, label_y, f'{name}: {sc:+.2f}{marker}',
                ha='center', va='bottom', fontsize=8.5,
                color=color, fontweight='bold', zorder=8,
                bbox=dict(fc='white', alpha=0.85, ec='none', pad=1))

    ax.set_title('Diagram 2 — Score Propagation  (Bottom-Up)',
                 fontsize=13, fontweight='bold', pad=14)

    legend_handles = [
        mpatches.Patch(fc='#D1FAE5', ec='#059669', label='Turn 3 leaf (pre-scored)'),
        mpatches.Patch(fc='#EDE9FE', ec='#7C3AED', label='Turn 2 node (scored bottom-up)'),
        mpatches.Patch(fc='#DBEAFE', ec='#1D4ED8', label='Turn 1 node (scored bottom-up)'),
        mpatches.Patch(fc='#FEF3C7', ec='#B45309', label='Root (returns best action)'),
    ]
    ax.legend(handles=legend_handles, loc='lower right', fontsize=8.5,
              framealpha=0.95, title='Legend', title_fontsize=9)

    plt.tight_layout()
    out = '/home/Fracture/PycharmProjects/NuzlockeSolver/diagram_scoring.png'
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f'Saved {out}')


if __name__ == '__main__':
    make_expansion()
    make_scoring()
