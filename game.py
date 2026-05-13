"""
VOID MAZE — v2.0
Changes from v1.3:
  - Main menu: SINGLE PLAYER / MULTIPLAYER / QUIT
  - Multiplayer: ESCAPE MODE and DEAD BY DAYLIGHT (host/join via Radmin VPN)
  - network.py module for TCP connection
  - Multiple player colors in MP
pip install pygame
"""

import pygame
import math
import random
import sys
from collections import deque

import network

pygame.init()

# ── layout ────────────────────────────────────────────────────────────────────
# Maze grid is FIXED across all players (so multiplayer maps are interchangeable).
# CELL size is computed per-machine so the maze scales to each player's screen.
COLS = 51   # must be odd
ROWS = 35   # must be odd

PANEL_W = 320

DISPLAY = pygame.display.Info()
SCREEN_W = DISPLAY.current_w
SCREEN_H = DISPLAY.current_h

# windowed mode: fit in screen with a small margin
W = SCREEN_W - 40
H = SCREEN_H - 80

# cell size to fit the maze inside (W - PANEL_W) x H
CELL = max(8, min((W - PANEL_W) // COLS, H // ROWS))

MAZE_W = COLS * CELL
MAZE_H = ROWS * CELL

# centre the maze within the area to the left of the panel
MAZE_OX = ((SCREEN_W - PANEL_W) - MAZE_W) // 2
MAZE_OY = (SCREEN_H - MAZE_H) // 2

# x coordinate where the panel begins
PANEL_X = SCREEN_W - PANEL_W

FPS = 60
MAZE_CHANGE_INTERVAL = 20.0

GATE_COUNT = 6
GATE_OPEN_TIME    = 1.2   # seconds to hold E to open a gate (old logic)
GATE_COLOR_CLOSED = (180, 120, 0)
GATE_COLOR_OPEN   = (80, 255, 80)

# ── colours ────────────────────────────────────────────────────────────────────
BLACK    = (0, 0, 0)
CYAN     = (0, 255, 200)
WHITE    = (255, 255, 255)
PANEL_BG = (8, 12, 24)

PORTAL_COLORS = [
    (0, 200, 255),
    (255, 160, 0),
    (80, 255, 80),
    (255, 80, 255),
    (255, 220, 0),
    (80, 80, 255),
]

# multiplayer: distinct colour per player slot
PLAYER_COLORS = [
    CYAN,
    (255, 160, 0),
    (80, 255, 80),
    (255, 80, 255),
]

MOVE_DELAY   = 0.11
HUNTER_DELAY = 0.38


# ── maze generation ───────────────────────────────────────────────────────────
def build_maze(rows, cols):
    walls   = [[True]  * cols for _ in range(rows)]
    visited = [[False] * cols for _ in range(rows)]

    stack = [(1, 1)]
    visited[1][1] = True
    walls[1][1]   = False

    dirs = [(0, 2), (2, 0), (0, -2), (-2, 0)]

    while stack:
        r, c = stack[-1]
        random.shuffle(dirs)
        moved = False

        for dr, dc in dirs:
            nr, nc = r + dr, c + dc
            if 0 < nr < rows - 1 and 0 < nc < cols - 1:
                if not visited[nr][nc]:
                    visited[nr][nc] = True
                    walls[nr][nc]   = False
                    walls[r + dr // 2][c + dc // 2] = False
                    stack.append((nr, nc))
                    moved = True
                    break

        if not moved:
            stack.pop()

    return walls


def is_wall(walls, r, c, gates=None):
    """Return True if (r,c) is a wall.  Open gates are treated as passable."""
    if r < 0 or r >= ROWS or c < 0 or c >= COLS:
        return True
    if walls[r][c]:
        if gates:
            for g in gates:
                if g.r == r and g.c == c and g.is_open:
                    return False
        return True
    return False


# ── nearest free cell ─────────────────────────────────────────────────────────
def nearest_free(walls, r, c, gates=None):
    """BFS from (r,c) to find the nearest non-wall cell."""
    if not is_wall(walls, r, c, gates):
        return r, c
    visited = set()
    q = deque([(r, c)])
    visited.add((r, c))
    while q:
        cr, cc = q.popleft()
        for dr, dc in ((0,1),(1,0),(0,-1),(-1,0)):
            nr, nc = cr + dr, cc + dc
            if (nr, nc) not in visited and 0 <= nr < ROWS and 0 <= nc < COLS:
                visited.add((nr, nc))
                if not is_wall(walls, nr, nc, gates):
                    return nr, nc
                q.append((nr, nc))
    return r, c   # fallback (should never happen in a valid maze)


# ── bfs for hunter ────────────────────────────────────────────────────────────
def bfs(walls, sr, sc, tr, tc, gates=None):
    if (sr, sc) == (tr, tc):
        return []

    dist = [[-1] * COLS for _ in range(ROWS)]
    prev = [[None]  * COLS for _ in range(ROWS)]

    q = deque([(sr, sc)])
    dist[sr][sc] = 0

    while q:
        r, c = q.popleft()
        if (r, c) == (tr, tc):
            break
        for dr, dc in ((0,1),(1,0),(0,-1),(-1,0)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < ROWS and 0 <= nc < COLS:
                if not is_wall(walls, nr, nc, gates) and dist[nr][nc] == -1:
                    dist[nr][nc] = dist[r][c] + 1
                    prev[nr][nc] = (r, c)
                    q.append((nr, nc))

    if dist[tr][tc] == -1:
        return None

    path, cur = [], (tr, tc)
    while cur != (sr, sc):
        path.append(cur)
        cur = prev[cur[0]][cur[1]]
    path.reverse()
    return path


# ── helpers ────────────────────────────────────────────────────────────────────
def free_cell(walls, exclude=None):
    exclude = set(exclude or [])
    for _ in range(1000):
        r = random.choice(range(1, ROWS - 1, 2))
        c = random.choice(range(1, COLS - 1, 2))
        if not walls[r][c] and (r, c) not in exclude:
            return r, c
    return None


def cell_xy(r, c):
    return (c * CELL + CELL // 2 + MAZE_OX, r * CELL + CELL // 2 + MAZE_OY)


# ── gate ──────────────────────────────────────────────────────────────────────
class Gate:
    """A wall cell that can be opened by holding E for GATE_OPEN_TIME seconds.

    Once open, stays open until Gate.close() is called (e.g. on maze shift).
    Progress resets if the player releases E before the timer is full.
    """

    def __init__(self, r, c):
        self.r         = r
        self.c         = c
        self.is_open   = False
        self._progress = {}   # entity_id → accumulated hold time

    def is_adjacent(self, r, c):
        return abs(r - self.r) + abs(c - self.c) == 1

    def update(self, dt, entity_id, pressing):
        prev = self._progress.get(entity_id, 0.0)
        self._progress[entity_id] = min(GATE_OPEN_TIME, prev + dt) if pressing else 0.0
        was_open     = self.is_open
        self.is_open = any(v >= GATE_OPEN_TIME for v in self._progress.values())
        return (not was_open) and self.is_open

    def close(self):
        self._progress = {}
        self.is_open   = False

    def player_progress(self):
        return self._progress.get("player", 0.0)

    def draw(self, surf, walls=None):
        x, y  = cell_xy(self.r, self.c)
        color = GATE_COLOR_OPEN if self.is_open else GATE_COLOR_CLOSED
        bar_w = CELL - 4
        bar_h = max(4, CELL // 5)
        rect  = pygame.Rect(x - bar_w // 2, y - bar_h // 2, bar_w, bar_h)
        pygame.draw.rect(surf, color, rect, border_radius=3)

        prog = self.player_progress()
        if prog > 0 and not self.is_open:
            frac     = prog / GATE_OPEN_TIME
            arc_rect = pygame.Rect(x - CELL//2 + 2, y - CELL//2 + 2, CELL - 4, CELL - 4)
            pygame.draw.arc(surf, (255, 220, 0), arc_rect,
                            math.pi / 2, math.pi / 2 + math.tau * frac, 3)

        if self.is_open:
            s = pygame.Surface((CELL, CELL), pygame.SRCALPHA)
            s.fill((80, 255, 80, 40))
            surf.blit(s, (self.c * CELL + MAZE_OX, self.r * CELL + MAZE_OY))


# ── DBD entities ──────────────────────────────────────────────────────────────
GEN_REPAIR_TIME    = 10.0   # seconds of held E to finish a generator
WAREHOUSE_IMPRISON = 30.0   # seconds before an imprisoned runner is eliminated
DBD_MATCH_LENGTH   = 7 * 60 # 7 minutes
DBD_GEN_COUNT      = 5
DBD_WAREHOUSE_COUNT = 3


class Generator:
    """A repairable machine. Runners hold E adjacent to fill its progress bar."""

    def __init__(self, r, c):
        self.r = r
        self.c = c
        self.progress  = 0.0     # 0 .. GEN_REPAIR_TIME
        self.completed = False

    def is_adjacent(self, r, c):
        return abs(r - self.r) + abs(c - self.c) <= 1   # same cell or 4-neighbour

    def draw(self, surf):
        x, y = cell_xy(self.r, self.c)
        size = int(CELL * 0.75)
        rect = pygame.Rect(x - size // 2, y - size // 2, size, size)

        if self.completed:
            pygame.draw.rect(surf, (80, 255, 80), rect, border_radius=4)
            pygame.draw.rect(surf, WHITE, rect, 2, border_radius=4)
            return

        # base box
        pygame.draw.rect(surf, (40, 80, 140), rect, border_radius=4)

        # in-box fill rises from the bottom as the repair progresses
        if self.progress > 0:
            frac = self.progress / GEN_REPAIR_TIME
            fill_h = max(2, int(size * frac))
            fill_rect = pygame.Rect(rect.x, rect.bottom - fill_h, size, fill_h)
            pygame.draw.rect(surf, (255, 220, 0), fill_rect, border_radius=4)

        pygame.draw.rect(surf, (120, 180, 255), rect, 2, border_radius=4)

        # thicker bar above the cell (always visible while repairing)
        if self.progress > 0:
            frac = self.progress / GEN_REPAIR_TIME
            bar_w = CELL - 6
            bar_h = max(6, CELL // 8)
            bx = x - bar_w // 2
            by = y - CELL // 2 - bar_h - 4
            pygame.draw.rect(surf, (30, 30, 30), (bx, by, bar_w, bar_h),
                             border_radius=2)
            pygame.draw.rect(surf, (255, 220, 0),
                             (bx, by, int(bar_w * frac), bar_h),
                             border_radius=2)
            pygame.draw.rect(surf, WHITE, (bx, by, bar_w, bar_h),
                             1, border_radius=2)


class Warehouse:
    """A holding cell. When the hunter brings a runner here they're imprisoned."""

    def __init__(self, r, c):
        self.r = r
        self.c = c
        self.imprisoned_pid = None   # player id currently held, or None

    def is_adjacent(self, r, c):
        return abs(r - self.r) + abs(c - self.c) <= 1

    def draw(self, surf):
        x, y = cell_xy(self.r, self.c)
        size = int(CELL * 0.8)
        rect = pygame.Rect(x - size // 2, y - size // 2, size, size)
        pygame.draw.rect(surf, (60, 60, 70), rect, border_radius=4)
        pygame.draw.rect(surf, (140, 140, 160), rect, 2, border_radius=4)
        # prison-bar pattern
        for i in range(3):
            bx = rect.x + 6 + i * (size // 3)
            pygame.draw.line(surf, (180, 180, 200),
                             (bx, rect.y + 4), (bx, rect.bottom - 4), 1)


# ── portal ─────────────────────────────────────────────────────────────────────
class Portal:
    def __init__(self, r, c, pair_idx, color):
        self.r        = r
        self.c        = c
        self.pair_idx = pair_idx
        self.color    = color
        self.phase    = random.uniform(0, math.tau)

    def draw(self, surf, t):
        x, y  = cell_xy(self.r, self.c)
        pulse = 0.65 + 0.35 * math.sin(t * 3 + self.phase)
        size  = int(CELL * 0.42 * pulse)

        for ring in range(4, 0, -1):
            radius = size * ring // 4
            glow   = pygame.Surface((radius * 2 + 4, radius * 2 + 4), pygame.SRCALPHA)
            pygame.draw.circle(glow, (*self.color, int(40 * ring * pulse)),
                               (radius + 2, radius + 2), radius, 2)
            surf.blit(glow, (x - radius - 2, y - radius - 2))

        pygame.draw.circle(surf, self.color, (x, y), max(6, size // 2))
        pygame.draw.circle(surf, WHITE,      (x, y), max(3, size // 5))


# ── player ─────────────────────────────────────────────────────────────────────
class Player:
    def __init__(self, r, c):
        self.r = r
        self.c = c
        self.trail          = deque(maxlen=16)
        self.teleport_flash = 0.0

    def draw(self, surf):
        for i, (tr, tc) in enumerate(self.trail):
            frac  = i / max(1, len(self.trail))
            size  = max(2, int(CELL * 0.12 * frac))
            tx, ty = cell_xy(tr, tc)
            trail = pygame.Surface((size * 2, size * 2), pygame.SRCALPHA)
            pygame.draw.circle(trail, (0, 255, 200, int(120 * frac)), (size, size), size)
            surf.blit(trail, (tx - size, ty - size))

        x, y   = cell_xy(self.r, self.c)
        radius = int(CELL * 0.30)
        glow   = pygame.Surface((radius * 4, radius * 4), pygame.SRCALPHA)
        pygame.draw.circle(glow, (0, 255, 200, 60), (radius * 2, radius * 2), radius * 2)
        surf.blit(glow, (x - radius * 2, y - radius * 2))
        pygame.draw.circle(surf, CYAN, (x, y), radius)


# ── hunter ─────────────────────────────────────────────────────────────────────
class Hunter:
    def __init__(self, r, c):
        self.r     = r
        self.c     = c
        self.trail = deque(maxlen=12)

    def draw(self, surf):
        for i, (tr, tc) in enumerate(self.trail):
            frac  = i / max(1, len(self.trail))
            size  = max(2, int(CELL * 0.11 * frac))
            tx, ty = cell_xy(tr, tc)
            trail = pygame.Surface((size * 2, size * 2), pygame.SRCALPHA)
            pygame.draw.circle(trail, (255, 60, 60, int(90 * frac)), (size, size), size)
            surf.blit(trail, (tx - size, ty - size))

        x, y = cell_xy(self.r, self.c)
        pygame.draw.circle(surf, (255, 60, 60), (x, y), int(CELL * 0.30))


# ── panel toggle button ────────────────────────────────────────────────────────
class ToggleButton:
    W = 260
    H = 38

    def __init__(self, label, x, y, state=True):
        self.label  = label
        self.rect   = pygame.Rect(x, y, self.W, self.H)
        self.state  = state   # True = ON

    def handle_click(self, pos):
        if self.rect.collidepoint(pos):
            self.state = not self.state
            return True
        return False

    def draw(self, surf, font):
        color_on  = (0, 180, 80)
        color_off = (120, 30, 30)
        color     = color_on if self.state else color_off

        pygame.draw.rect(surf, color,      self.rect, border_radius=6)
        pygame.draw.rect(surf, WHITE,      self.rect, 1, border_radius=6)

        tag  = "ON" if self.state else "OFF"
        text = font.render(f"{self.label}  [{tag}]", True, WHITE)
        surf.blit(text, (self.rect.x + 10, self.rect.y + (self.H - text.get_height()) // 2))


# ── game ───────────────────────────────────────────────────────────────────────
class Game:
    def __init__(self):
        # Windowed mode
        self.surf  = pygame.display.set_mode((W, H))
        pygame.display.set_caption("VOID MAZE v2.0")
        self.clock = pygame.time.Clock()

        self.font_xl  = pygame.font.Font(None, 72)
        self.font_lg  = pygame.font.Font(None, 54)
        self.font_med = pygame.font.Font(None, 40)
        self.font_sm  = pygame.font.Font(None, 30)

        # ── toggle buttons (positioned in panel) ─────────────────────────────
        bx = PANEL_X + 30
        self.btn_maze    = ToggleButton("MAZE SHIFT",  bx, H - 160, state=True)
        self.btn_hunter  = ToggleButton("HUNTER",      bx, H - 112, state=True)
        self.btn_portals = ToggleButton("PORTALS",     bx, H -  64, state=True)

        # menu state
        self.menu_options = ["SINGLE PLAYER", "MULTIPLAYER", "QUIT"]
        self.menu_index   = 0
        self.menu_rects   = []   # populated each frame in draw_menu

        # lobby state
        self.lobby_options = ["HOST GAME", "JOIN GAME", "BACK"]
        self.lobby_index   = 0
        self.lobby_rects   = []

        # mode picker (host only, between lobby and wait_host)
        self.mode_options = ["ESCAPE MODE", "DEAD BY DAYLIGHT", "BACK"]
        self.mode_index   = 0
        self.mode_rects   = []
        self.mp_mode      = "escape"   # "escape" or "dbd"

        # ESCAPE-mode settings (host-decided in lobby_wait_host)
        self.mp_settings = {"portals": True, "maze_shift": True, "hunter": "bot"}
        self.settings_rows  = ["PORTALS", "MAZE SHIFT", "HUNTER"]
        self.settings_index = 0

        # match-end screen
        self.mp_winner    = ""   # "runners" / "hunter" / ""
        self.mp_end_rects = {}   # "replay" / "menu" → pygame.Rect for clicks

        # multiplayer state
        self.server          = None   # network.Server when hosting
        self.client          = None   # network.Client when joining
        self.player_id       = 0      # 0 for host, 1..3 for clients
        self.mp_players      = []     # list of player dicts (see host_start_match)
        self.mp_gates_open   = []     # parallel list to self.gates
        self.mp_text_input   = ""     # for typing IP address
        self.mp_status_msg   = ""     # info/error line shown in lobby screens
        self.mp_move_timers  = [0.0] * network.MAX_PLAYERS   # host-side per-player move cd
        self.mp_pending_input = {}    # host-side: latest input per player id
        self.mp_local_input   = {"dr": 0, "dc": 0, "e_held": False}  # client-side
        self.mp_send_timer    = 0.0   # client throttle for sending input
        self.mp_broadcast_timer = 0.0 # host throttle for broadcasting state

        # DBD-specific shared state
        self.mp_generators   = []     # list of Generator
        self.mp_warehouses   = []     # list of Warehouse
        self.mp_match_timer  = 0.0    # countdown seconds remaining in DBD match
        self.exit_unlocked   = True   # escape: always True; DBD: False until all gens done

        self.state = "menu"
        self.reset()

    # ── reset / level ─────────────────────────────────────────────────────────
    def reset(self):
        self.level        = 1
        self.score        = 0
        self.portal_count = 0
        self.new_level()

    def new_level(self):
        self.walls = build_maze(ROWS, COLS)

        self.player = Player(1, 1)
        self.hunter = Hunter(ROWS - 2, COLS - 2)
        self.exit_pos = (ROWS - 2, COLS - 2)

        self.portals = []
        self.gates   = []

        self.move_timer    = 0.0
        self.hunter_timer  = 0.0
        self.maze_timer    = 0.0

        self.build_portals()
        self.build_gates()

    # ── maze shift ────────────────────────────────────────────────────────────
    def shift_maze(self):
        """Regenerate maze, keep player/hunter, snap to nearest free cell."""
        pr, pc = self.player.r, self.player.c
        hr, hc = self.hunter.r, self.hunter.c

        self.walls = build_maze(ROWS, COLS)
        self.gates = []
        self.build_gates()

        # portals stay referenced to old positions — rebuild them too
        self.portals = []
        self.build_portals()

        # snap entities to nearest free cell
        self.player.r, self.player.c = nearest_free(self.walls, pr, pc, self.gates)
        self.hunter.r, self.hunter.c = nearest_free(self.walls, hr, hc, self.gates)
        self.player.trail.clear()
        self.hunter.trail.clear()

    # ── portals ───────────────────────────────────────────────────────────────
    def build_portals(self):
        taken = [(1, 1), self.exit_pos]

        for i in range(6):
            a = free_cell(self.walls, taken)
            if a is None:
                break
            taken.append(a)

            b = free_cell(self.walls, taken)
            if b is None:
                break
            taken.append(b)

            idx   = len(self.portals)
            color = PORTAL_COLORS[i % len(PORTAL_COLORS)]

            self.portals.append(Portal(a[0], a[1], idx + 1, color))
            self.portals.append(Portal(b[0], b[1], idx,     color))

    # ── gates ─────────────────────────────────────────────────────────────────
    def build_gates(self):
        """Pick wall cells that form a clean 2-case door: either walls above+below
        with free cells left+right (player passes horizontally — vertical bar),
        OR walls left+right with free cells above+below (player passes vertically
        — horizontal bar). T-junctions and corners are skipped.
        """
        candidates = [
            (r, c)
            for r in range(1, ROWS - 1)
            for c in range(1, COLS - 1)
            if self.walls[r][c]
        ]
        random.shuffle(candidates)

        for r, c in candidates:
            if len(self.gates) >= GATE_COUNT:
                break
            w_above = self.walls[r - 1][c]
            w_below = self.walls[r + 1][c]
            w_left  = self.walls[r][c - 1]
            w_right = self.walls[r][c + 1]
            vert_case = w_above and w_below and (not w_left) and (not w_right)
            horz_case = w_left  and w_right and (not w_above) and (not w_below)
            if vert_case or horz_case:
                self.gates.append(Gate(r, c))

    # ── drawing ───────────────────────────────────────────────────────────────
    def draw_maze(self):
        self.surf.fill(BLACK)

        gate_cells = {(g.r, g.c) for g in self.gates}

        for r in range(ROWS):
            for c in range(COLS):
                if self.walls[r][c] and (r, c) not in gate_cells:
                    rect = pygame.Rect(c * CELL + MAZE_OX, r * CELL + MAZE_OY,
                                       CELL, CELL)
                    pygame.draw.rect(self.surf, (0, 40, 70),   rect)
                    pygame.draw.rect(self.surf, (0, 90, 140),  rect, 1)

    def draw_exit(self):
        er, ec = self.exit_pos
        x, y   = cell_xy(er, ec)
        size   = int(CELL * 0.35)
        points = [(x, y - size), (x + size, y), (x, y + size), (x - size, y)]
        pygame.draw.polygon(self.surf, WHITE, points)

    def draw_maze_timer_bar(self):
        """Show a thin bar at the top of the maze indicating time until next shift."""
        if not self.btn_maze.state:
            return
        frac  = self.maze_timer / MAZE_CHANGE_INTERVAL
        bar_w = int(MAZE_W * frac)
        pygame.draw.rect(self.surf, (0, 60, 100),  (MAZE_OX, MAZE_OY, MAZE_W, 4))
        pygame.draw.rect(self.surf, (0, 200, 255), (MAZE_OX, MAZE_OY, bar_w, 4))

    def draw_panel(self):
        pygame.draw.rect(self.surf, PANEL_BG, (PANEL_X, 0, PANEL_W, H))
        pygame.draw.line(self.surf, (0, 120, 200), (PANEL_X, 0), (PANEL_X, H), 3)

        title = self.font_lg.render("VOID MAZE", True, CYAN)
        self.surf.blit(title, (PANEL_X + PANEL_W // 2 - title.get_width() // 2, 24))

        stats = [
            f"LEVEL   : {self.level}",
            f"SCORE   : {self.score}",
            f"PORTALS : {self.portal_count}",
        ]
        for i, text in enumerate(stats):
            s = self.font_med.render(text, True, WHITE)
            self.surf.blit(s, (PANEL_X + 24, 110 + i * 48))

        # countdown label
        if self.btn_maze.state:
            secs  = max(0, MAZE_CHANGE_INTERVAL - self.maze_timer)
            label = self.font_sm.render(f"SHIFT IN : {secs:.1f}s", True, (0, 200, 255))
            self.surf.blit(label, (PANEL_X + 24, 110 + 3 * 48))

        # controls hint
        hint_y = H - 200
        hint   = self.font_sm.render("HOLD E : open gate", True, (160, 160, 160))
        self.surf.blit(hint, (PANEL_X + 24, hint_y))

        # toggle buttons
        self.btn_maze.draw(self.surf,    self.font_sm)
        self.btn_hunter.draw(self.surf,  self.font_sm)
        self.btn_portals.draw(self.surf, self.font_sm)

    # ── movement ──────────────────────────────────────────────────────────────
    def try_move(self, dr, dc):
        nr = self.player.r + dr
        nc = self.player.c + dc

        if is_wall(self.walls, nr, nc, self.gates):
            return

        self.player.trail.append((self.player.r, self.player.c))
        self.player.r = nr
        self.player.c = nc
        self.score += 1

        if self.btn_portals.state:
            self.check_portal()
        self.check_exit()
        self.check_caught()

    # ── gate logic ────────────────────────────────────────────────────────────
    def update_gates(self, dt, e_held):
        for gate in self.gates:
            player_adj = gate.is_adjacent(self.player.r, self.player.c)
            gate.update(dt, "player", e_held and player_adj)

    # ── checks ────────────────────────────────────────────────────────────────
    def check_portal(self):
        for p in self.portals:
            if (p.r, p.c) == (self.player.r, self.player.c):
                dest = self.portals[p.pair_idx]
                self.player.r = dest.r
                self.player.c = dest.c
                self.score       += 15
                self.portal_count += 1
                return

    def check_exit(self):
        if (self.player.r, self.player.c) == self.exit_pos:
            self.score += 100
            self.level += 1
            self.new_level()

    def check_caught(self):
        if (self.player.r, self.player.c) == (self.hunter.r, self.hunter.c):
            self.state = "dead"

    # ── hunter AI ─────────────────────────────────────────────────────────────
    def update_hunter(self, dt):
        if not self.btn_hunter.state:
            return

        self.hunter_timer += dt
        if self.hunter_timer < HUNTER_DELAY:
            return
        self.hunter_timer = 0

        path = bfs(self.walls, self.hunter.r, self.hunter.c,
                   self.player.r, self.player.c, self.gates)

        if path:
            self.hunter.trail.append((self.hunter.r, self.hunter.c))
            self.hunter.r, self.hunter.c = path[0]

        self.check_caught()

    # ── overlay ───────────────────────────────────────────────────────────────
    def draw_overlay(self, title, lines, color):
        ow, oh = 700, 300
        ox = MAZE_OX + MAZE_W // 2 - ow // 2
        oy = MAZE_OY + MAZE_H // 2 - oh // 2

        overlay = pygame.Surface((ow, oh), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 220))
        self.surf.blit(overlay, (ox, oy))
        pygame.draw.rect(self.surf, color, (ox, oy, ow, oh), 3)

        ts = self.font_xl.render(title, True, color)
        self.surf.blit(ts, (ox + ow // 2 - ts.get_width() // 2, oy + 30))

        for i, line in enumerate(lines):
            ls = self.font_med.render(line, True, WHITE)
            self.surf.blit(ls, (ox + ow // 2 - ls.get_width() // 2, oy + 120 + i * 40))

    # ── menu ──────────────────────────────────────────────────────────────────
    def select_menu_option(self):
        choice = self.menu_options[self.menu_index]
        if choice == "SINGLE PLAYER":
            self.reset()
            self.state = "title"
        elif choice == "MULTIPLAYER":
            self.mp_status_msg = ""
            self.state = "lobby"
        elif choice == "QUIT":
            pygame.quit()
            sys.exit()

    def handle_menu_click(self, pos):
        for i, rect in enumerate(self.menu_rects):
            if rect.collidepoint(pos):
                self.menu_index = i
                self.select_menu_option()
                return True
        return False

    # ── lobby (host/join chooser) ─────────────────────────────────────────────
    def select_lobby_option(self):
        choice = self.lobby_options[self.lobby_index]
        if choice == "HOST GAME":
            self.mode_index = 0
            self.mp_status_msg = ""
            self.state = "lobby_mode_pick"
        elif choice == "JOIN GAME":
            self.mp_text_input = ""
            self.mp_status_msg = ""
            self.state = "lobby_join_input"
        elif choice == "BACK":
            self.state = "menu"

    # ── mode picker (host only) ───────────────────────────────────────────────
    def select_mode_option(self):
        choice = self.mode_options[self.mode_index]
        if choice == "ESCAPE MODE":
            self.mp_mode = "escape"
            self.start_host_mode()
        elif choice == "DEAD BY DAYLIGHT":
            self.mp_mode = "dbd"
            self.start_host_mode()
        elif choice == "BACK":
            self.state = "lobby"

    def handle_mode_click(self, pos):
        for i, rect in enumerate(self.mode_rects):
            if rect.collidepoint(pos):
                self.mode_index = i
                self.select_mode_option()
                return True
        return False

    def draw_lobby_mode_pick(self):
        self.surf.fill(BLACK)

        title = self.font_xl.render("CHOOSE MODE", True, CYAN)
        self.surf.blit(title, (W // 2 - title.get_width() // 2, H // 5))

        descriptions = {
            "ESCAPE MODE":
                "Co-op: all players race to escape the maze. Bot hunter.",
            "DEAD BY DAYLIGHT":
                "Host plays the HUNTER. Runners repair 5 generators to unlock the exit.",
            "BACK":
                "Return to the lobby.",
        }

        self.mode_rects = []
        opt_h   = 70
        start_y = H // 2 - 40

        for i, label in enumerate(self.mode_options):
            selected = (i == self.mode_index)
            color    = CYAN if selected else WHITE
            text     = self.font_lg.render(label, True, color)
            tw, th   = text.get_size()
            x = W // 2 - tw // 2
            y = start_y + i * opt_h

            rect = pygame.Rect(x - 20, y - 6, tw + 40, th + 12)
            self.mode_rects.append(rect)

            if selected:
                pygame.draw.rect(self.surf, (0, 60, 90), rect, border_radius=6)
                pygame.draw.rect(self.surf, CYAN, rect, 2, border_radius=6)

            self.surf.blit(text, (x, y))

            desc = descriptions.get(label, "")
            if desc and selected:
                d = self.font_sm.render(desc, True, (160, 160, 160))
                self.surf.blit(d, (W // 2 - d.get_width() // 2, y + th + 14))

        hint = self.font_sm.render("UP/DOWN/ENTER  -  ESC = back",
                                   True, (120, 120, 120))
        self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 60))

    def handle_lobby_click(self, pos):
        for i, rect in enumerate(self.lobby_rects):
            if rect.collidepoint(pos):
                self.lobby_index = i
                self.select_lobby_option()
                return True
        return False

    def draw_lobby(self):
        self.surf.fill(BLACK)

        title = self.font_xl.render("MULTIPLAYER", True, CYAN)
        self.surf.blit(title, (W // 2 - title.get_width() // 2, H // 5))

        sub = self.font_sm.render("Host opens a server. Join connects to a host's Radmin IP.",
                                  True, (160, 160, 160))
        self.surf.blit(sub, (W // 2 - sub.get_width() // 2, H // 5 + 80))

        self.lobby_rects = []
        opt_h   = 60
        start_y = H // 2

        for i, label in enumerate(self.lobby_options):
            selected = (i == self.lobby_index)
            color    = CYAN if selected else WHITE
            text     = self.font_lg.render(label, True, color)
            tw, th   = text.get_size()
            x = W // 2 - tw // 2
            y = start_y + i * opt_h

            rect = pygame.Rect(x - 20, y - 6, tw + 40, th + 12)
            self.lobby_rects.append(rect)

            if selected:
                pygame.draw.rect(self.surf, (0, 60, 90), rect, border_radius=6)
                pygame.draw.rect(self.surf, CYAN, rect, 2, border_radius=6)

            self.surf.blit(text, (x, y))

        if self.mp_status_msg:
            msg = self.font_sm.render(self.mp_status_msg, True, (255, 200, 80))
            self.surf.blit(msg, (W // 2 - msg.get_width() // 2, H - 100))

        hint = self.font_sm.render("UP/DOWN/ENTER  -  ESC = back", True, (120, 120, 120))
        self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 60))

    # ── lobby_join_input ──────────────────────────────────────────────────────
    def draw_lobby_join_input(self):
        self.surf.fill(BLACK)

        title = self.font_xl.render("JOIN GAME", True, CYAN)
        self.surf.blit(title, (W // 2 - title.get_width() // 2, H // 5))

        prompt = self.font_med.render("Enter host IP (Radmin VPN):", True, WHITE)
        self.surf.blit(prompt, (W // 2 - prompt.get_width() // 2, H // 2 - 60))

        # text box
        box_w, box_h = 480, 60
        bx = W // 2 - box_w // 2
        by = H // 2
        pygame.draw.rect(self.surf, (20, 30, 50), (bx, by, box_w, box_h),
                         border_radius=6)
        pygame.draw.rect(self.surf, CYAN, (bx, by, box_w, box_h),
                         2, border_radius=6)

        txt = self.font_lg.render(self.mp_text_input + "_", True, WHITE)
        self.surf.blit(txt, (bx + 14, by + (box_h - txt.get_height()) // 2))

        port_hint = self.font_sm.render(
            f"Port: {network.DEFAULT_PORT}  -  e.g. 26.123.45.6",
            True, (160, 160, 160))
        self.surf.blit(port_hint, (W // 2 - port_hint.get_width() // 2,
                                   H // 2 + box_h + 14))

        if self.mp_status_msg:
            msg = self.font_sm.render(self.mp_status_msg, True, (255, 120, 120))
            self.surf.blit(msg, (W // 2 - msg.get_width() // 2,
                                 H // 2 + box_h + 50))

        hint = self.font_sm.render("ENTER = connect  -  ESC = back", True, (120, 120, 120))
        self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 60))

    # ── ESCAPE-mode settings (host adjusts in the wait room) ──────────────────
    def _settings_value_str(self, key):
        v = self.mp_settings.get(key)
        if isinstance(v, bool):
            return "ON" if v else "OFF"
        return str(v).upper()

    def cycle_setting(self, key, direction):
        """direction: +1 or -1.  Booleans flip; HUNTER cycles OFF/BOT/PLAYER."""
        v = self.mp_settings.get(key)
        if isinstance(v, bool):
            self.mp_settings[key] = not v
        elif key == "hunter":
            choices = ["off", "bot", "player"]
            idx = choices.index(v) if v in choices else 1
            self.mp_settings[key] = choices[(idx + direction) % len(choices)]

    # ── lobby_wait (host or client waiting room) ──────────────────────────────
    def draw_lobby_wait(self):
        self.surf.fill(BLACK)

        if self.server is not None:
            title = self.font_xl.render("HOSTING", True, CYAN)
            self.surf.blit(title, (W // 2 - title.get_width() // 2, 60))

            mode_label = "ESCAPE MODE" if self.mp_mode == "escape" else "DEAD BY DAYLIGHT"
            mode_text = self.font_med.render(f"Mode: {mode_label}",
                                             True, (255, 220, 80))
            self.surf.blit(mode_text,
                           (W // 2 - mode_text.get_width() // 2, 140))

            ip_hint = self.font_sm.render(
                f"Share your Radmin IP with players  -  Port {self.server.port}",
                True, (160, 160, 160))
            self.surf.blit(ip_hint, (W // 2 - ip_hint.get_width() // 2, 190))

            n = self.server.count()
            count = self.font_lg.render(f"Players: {n + 1} / {network.MAX_PLAYERS}",
                                        True, WHITE)
            self.surf.blit(count, (W // 2 - count.get_width() // 2, 240))

            # ESCAPE: host-decided settings (DBD has fixed settings)
            if self.mp_mode == "escape":
                head = self.font_med.render("SETTINGS", True, WHITE)
                self.surf.blit(head, (W // 2 - head.get_width() // 2, 320))

                row_h = 50
                start_y = 370
                for i, key_name in enumerate(self.settings_rows):
                    key  = key_name.lower().replace(" ", "_")
                    val  = self._settings_value_str(key)
                    sel  = (i == self.settings_index)
                    col  = CYAN if sel else WHITE
                    text = self.font_med.render(f"{key_name:<12s}  <  {val}  >",
                                                True, col)
                    tw, th = text.get_size()
                    x = W // 2 - tw // 2
                    y = start_y + i * row_h
                    if sel:
                        pygame.draw.rect(self.surf, (0, 60, 90),
                                         (x - 16, y - 4, tw + 32, th + 8),
                                         border_radius=4)
                    self.surf.blit(text, (x, y))

                hint1 = self.font_sm.render(
                    "UP/DOWN: select  -  LEFT/RIGHT: change value",
                    True, (160, 160, 160))
                self.surf.blit(hint1, (W // 2 - hint1.get_width() // 2,
                                       start_y + len(self.settings_rows) * row_h + 30))

            hint = self.font_med.render("SPACE = start  -  ESC = cancel",
                                        True, (200, 200, 200))
            self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 90))

        elif self.client is not None:
            title = self.font_xl.render("CONNECTED", True, CYAN)
            self.surf.blit(title, (W // 2 - title.get_width() // 2, H // 5))

            wait = self.font_lg.render(f"You are PLAYER {self.player_id + 1}",
                                       True, PLAYER_COLORS[self.player_id])
            self.surf.blit(wait, (W // 2 - wait.get_width() // 2, H // 2 - 30))

            msg = self.font_med.render("Waiting for host to press SPACE...",
                                       True, (200, 200, 200))
            self.surf.blit(msg, (W // 2 - msg.get_width() // 2, H // 2 + 40))

            hint = self.font_sm.render("ESC = disconnect", True, (120, 120, 120))
            self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 60))

    def draw_menu(self):
        self.surf.fill(BLACK)

        # title
        title = self.font_xl.render("VOID MAZE", True, CYAN)
        self.surf.blit(title, (W // 2 - title.get_width() // 2, H // 4))

        subtitle = self.font_sm.render("SELECT MODE", True, (160, 160, 160))
        self.surf.blit(subtitle, (W // 2 - subtitle.get_width() // 2, H // 4 + 80))

        # options
        self.menu_rects = []
        opt_h = 60
        start_y = H // 2

        for i, label in enumerate(self.menu_options):
            selected = (i == self.menu_index)
            color    = CYAN if selected else WHITE
            text     = self.font_lg.render(label, True, color)
            tw, th   = text.get_size()
            x = W // 2 - tw // 2
            y = start_y + i * opt_h

            rect = pygame.Rect(x - 20, y - 6, tw + 40, th + 12)
            self.menu_rects.append(rect)

            if selected:
                pygame.draw.rect(self.surf, (0, 60, 90), rect, border_radius=6)
                pygame.draw.rect(self.surf, CYAN, rect, 2, border_radius=6)

            self.surf.blit(text, (x, y))

        # footer hint
        hint = self.font_sm.render("UP/DOWN to navigate, ENTER to select, ESC to quit",
                                   True, (120, 120, 120))
        self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 60))

    # ── multiplayer: host/client lifecycle ────────────────────────────────────
    def start_host_mode(self):
        """Open a TCP server on DEFAULT_PORT and become player 0."""
        try:
            self.server = network.Server()
        except Exception as e:
            self.mp_status_msg = f"Server failed to start: {e}"
            return
        self.player_id = 0
        self.mp_status_msg = ""
        self.state = "lobby_wait_host"

    def start_client_mode(self, ip):
        """Connect to a host's TCP server. We get our player_id back via 'welcome'."""
        try:
            self.client = network.Client(ip.strip(), network.DEFAULT_PORT)
        except Exception as e:
            self.mp_status_msg = f"Connection failed: {e}"
            self.client = None
            return
        # send hello, wait briefly for welcome (handled in tick)
        self.client.send({"type": "hello"})
        self.mp_status_msg = ""
        self.state = "lobby_wait_client"

    def mp_disconnect(self):
        """Tear down any open server/client; return to main menu."""
        if self.server is not None:
            try: self.server.close()
            except Exception: pass
            self.server = None
        if self.client is not None:
            try: self.client.close()
            except Exception: pass
            self.client = None
        self.mp_players      = []
        self.mp_pending_input = {}
        self.mp_generators   = []
        self.mp_warehouses   = []
        self.mp_winner       = ""
        self.exit_unlocked   = True
        self.player_id        = 0
        self.state            = "menu"

    # ── host: start a multiplayer match ───────────────────────────────────────
    def host_start_match(self):
        """Host pressed SPACE — initialise level and broadcast maze + start."""
        # clean any dead connections BEFORE locking in player_id assignments
        self.server.prune_dead()

        self.new_level()
        self.mp_generators = []
        self.mp_warehouses = []
        self.mp_winner     = ""
        self.mp_match_timer = DBD_MATCH_LENGTH

        # initial player spots: corners-ish around (1, 1) for runners,
        # opposite corner for hunter in DBD
        runner_spawns = [(1, 1), (1, 3), (3, 1), (3, 3)]
        slots = 1 + self.server.count()
        self.mp_players = []

        if self.mp_mode == "escape":
            self.exit_unlocked = True
            # apply host-decided ESCAPE settings
            if not self.mp_settings.get("portals", True):
                self.portals = []

            hunter_setting = self.mp_settings.get("hunter", "bot")
            hunter_idx = -1
            if hunter_setting == "off":
                self.hunter = None
            elif hunter_setting == "player":
                self.hunter = None   # no bot — a player is the hunter
                hunter_idx = random.randrange(slots)
            # "bot" → keep self.hunter from new_level()

            runner_seen = 0
            for i in range(slots):
                if i == hunter_idx:
                    r, c = nearest_free(self.walls, ROWS // 2, COLS // 2,
                                        self.gates)
                    role = "hunter"
                else:
                    sr, sc = runner_spawns[runner_seen] \
                             if runner_seen < len(runner_spawns) else (1, 1)
                    r, c = nearest_free(self.walls, sr, sc, self.gates)
                    role = "runner"
                    runner_seen += 1
                self.mp_players.append({
                    "id": i, "r": r, "c": c, "alive": True,
                    "role": role,
                    "imprisoned": False, "imprison_remaining": 0.0,
                })
        else:  # dbd
            self.exit_unlocked = False
            # randomly pick which slot is the hunter
            hunter_idx = random.randrange(slots)
            # spawn hunter at the centre of the maze (NOT at the exit corner)
            hunter_r, hunter_c = nearest_free(self.walls,
                                              ROWS // 2, COLS // 2, self.gates)
            runner_seen = 0
            for i in range(slots):
                if i == hunter_idx:
                    r, c, role = hunter_r, hunter_c, "hunter"
                else:
                    sr, sc = runner_spawns[runner_seen] \
                             if runner_seen < len(runner_spawns) else (1, 1)
                    r, c = nearest_free(self.walls, sr, sc, self.gates)
                    role = "runner"
                    runner_seen += 1
                self.mp_players.append({
                    "id": i, "r": r, "c": c, "alive": True,
                    "role": role,
                    "imprisoned": False, "imprison_remaining": 0.0,
                })
            # place generators + warehouses on free cells
            taken = [(p["r"], p["c"]) for p in self.mp_players]
            for _ in range(DBD_GEN_COUNT):
                cell = free_cell(self.walls, taken)
                if cell is None: break
                taken.append(cell)
                self.mp_generators.append(Generator(cell[0], cell[1]))
            for _ in range(DBD_WAREHOUSE_COUNT):
                cell = free_cell(self.walls, taken)
                if cell is None: break
                taken.append(cell)
                self.mp_warehouses.append(Warehouse(cell[0], cell[1]))
            # DBD: no portals (rebuild without them) and disable hunter bot via mode flag
            self.portals = []

        self.mp_move_timers = [0.0] * network.MAX_PLAYERS

        # broadcast maze then start (include mode + DBD entities)
        self.server.broadcast(self._serialize_maze())
        self.server.broadcast({"type": "start", "mode": self.mp_mode})
        self.state = "mp_play"

    def _serialize_maze(self):
        """Compact maze description sent on level start / maze shift."""
        wall_rows = ["".join("1" if w else "0" for w in row) for row in self.walls]
        return {
            "type":    "maze",
            "mode":    self.mp_mode,
            "rows":    ROWS,
            "cols":    COLS,
            "walls":   wall_rows,
            "exit":    list(self.exit_pos),
            "gates":   [[g.r, g.c] for g in self.gates],
            "portals": [[p.r, p.c, p.pair_idx, list(p.color)] for p in self.portals],
            "generators": [[g.r, g.c] for g in self.mp_generators],
            "warehouses": [[w.r, w.c] for w in self.mp_warehouses],
        }

    def _apply_maze(self, msg):
        """Client-side: rebuild local maze structures from a 'maze' message."""
        self.mp_mode = msg.get("mode", "escape")
        self.walls = [[c == "1" for c in row] for row in msg["walls"]]
        self.exit_pos = tuple(msg["exit"])
        self.gates = [Gate(r, c) for r, c in msg["gates"]]
        self.portals = [Portal(r, c, pi, tuple(co))
                        for r, c, pi, co in msg["portals"]]
        self.mp_generators = [Generator(r, c) for r, c in msg.get("generators", [])]
        self.mp_warehouses = [Warehouse(r, c) for r, c in msg.get("warehouses", [])]

    def _serialize_state(self):
        hunter_pos = None
        if self.mp_mode == "escape" and self.hunter is not None:
            hunter_pos = [self.hunter.r, self.hunter.c]
        return {
            "type":    "state",
            "players": self.mp_players,
            "hunter":  hunter_pos,
            "gates":   [g.is_open for g in self.gates],
            "level":   self.level,
            "gens":    [[g.progress, g.completed] for g in self.mp_generators],
            "wh":      [w.imprisoned_pid for w in self.mp_warehouses],
            "timer":   self.mp_match_timer,
            "exit_unlocked": self.exit_unlocked,
        }

    def _apply_state(self, msg):
        self.mp_players = msg["players"]
        hp = msg.get("hunter")
        if hp is not None:
            if self.hunter is None:
                self.hunter = Hunter(*hp)
            else:
                self.hunter.r, self.hunter.c = hp
        else:
            self.hunter = None
        opens = msg.get("gates", [])
        for i, g in enumerate(self.gates):
            if i < len(opens):
                g.is_open = bool(opens[i])
        self.level = msg.get("level", self.level)
        gens = msg.get("gens", [])
        for i, g in enumerate(self.mp_generators):
            if i < len(gens):
                g.progress, g.completed = gens[i]
        wh = msg.get("wh", [])
        for i, w in enumerate(self.mp_warehouses):
            if i < len(wh):
                w.imprisoned_pid = wh[i]
        self.mp_match_timer = msg.get("timer", self.mp_match_timer)
        self.exit_unlocked  = msg.get("exit_unlocked", True)

    # ── host: per-frame tick ──────────────────────────────────────────────────
    def mp_host_tick(self, dt):
        """Authoritative game step + state broadcast (branches on mp_mode)."""
        # 1. ingest client messages
        for ci, msg in self.server.drain_all():
            if msg.get("type") == "input":
                pid = ci + 1
                if pid < len(self.mp_players):
                    self.mp_pending_input[pid] = msg

        # mark disconnected clients as dead
        for ci in self.server.dead_indices():
            pid = ci + 1
            if pid < len(self.mp_players) and self.mp_players[pid]["alive"]:
                self.mp_players[pid]["alive"] = False

        # 2. host's own input as player 0
        if 0 not in self.mp_pending_input:
            self.mp_pending_input[0] = self.mp_local_input

        # 3. per-player movement (imprisoned players can't move)
        for p in self.mp_players:
            if not p["alive"] or p.get("imprisoned"):
                continue
            pid = p["id"]
            self.mp_move_timers[pid] += dt
            inp = self.mp_pending_input.get(pid, {"dr": 0, "dc": 0, "e_held": False})
            dr, dc = int(inp.get("dr", 0)), int(inp.get("dc", 0))
            if (dr or dc) and self.mp_move_timers[pid] >= MOVE_DELAY:
                nr, nc = p["r"] + dr, p["c"] + dc
                if not is_wall(self.walls, nr, nc, self.gates):
                    p["r"], p["c"] = nr, nc
                    self.mp_move_timers[pid] = 0.0
                    # portal teleport (escape mode only — DBD has no portals)
                    if self.mp_mode == "escape":
                        for portal in self.portals:
                            if (portal.r, portal.c) == (nr, nc):
                                dest = self.portals[portal.pair_idx]
                                p["r"], p["c"] = dest.r, dest.c
                                break
                    # exit reached
                    if (p["r"], p["c"]) == self.exit_pos:
                        if self.mp_mode == "escape":
                            # only a runner triggers exit; hunter walking on it does nothing
                            if p.get("role", "runner") != "runner":
                                pass
                            elif self.mp_settings.get("hunter") == "player":
                                # player hunter mode: first runner to exit wins the match
                                self.mp_winner = "runners"
                                self.state    = "mp_end"
                                self.server.broadcast(self._serialize_state())
                                self.server.broadcast({"type": "end", "winner": "runners"})
                                return
                            else:
                                self.level += 1
                                self.host_advance_level()
                                return
                        elif self.mp_mode == "dbd" and p["role"] == "runner" \
                             and self.exit_unlocked:
                            self.mp_winner = "runners"
                            self.state = "mp_end"
                            self.server.broadcast({"type": "end", "winner": "runners"})
                            return

        # 4. gates — any adjacent alive player holding E accumulates progress
        for gate in self.gates:
            for p in self.mp_players:
                if p["alive"] and not p.get("imprisoned") \
                   and gate.is_adjacent(p["r"], p["c"]):
                    inp = self.mp_pending_input.get(p["id"], {})
                    pressing = bool(inp.get("e_held"))
                    gate.update(dt, f"player_{p['id']}", pressing)
                else:
                    gate.update(dt, f"player_{p['id']}", False)

        # 5. mode-specific logic
        if self.mp_mode == "escape":
            # PLAYER hunter: any runner sharing the hunter's cell is eliminated
            if self.mp_settings.get("hunter") == "player":
                hp = next((p for p in self.mp_players
                           if p.get("role") == "hunter" and p["alive"]), None)
                if hp is not None:
                    for p in self.mp_players:
                        if p.get("role") == "runner" and p["alive"] \
                           and (p["r"], p["c"]) == (hp["r"], hp["c"]):
                            p["alive"] = False
                    runners = [p for p in self.mp_players
                               if p.get("role") == "runner"]
                    if runners and all(not p["alive"] for p in runners):
                        self.mp_winner = "hunter"
                        self.state     = "mp_end"
                        self.server.broadcast(self._serialize_state())
                        self.server.broadcast({"type": "end", "winner": "hunter"})
                        return

            # bot hunter (only if HUNTER setting is "bot" and we have one)
            if self.mp_settings.get("hunter", "bot") == "bot" and self.hunter is not None:
                self.hunter_timer += dt
                if self.hunter_timer >= HUNTER_DELAY:
                    self.hunter_timer = 0.0
                    target = self._nearest_alive_to(self.hunter.r, self.hunter.c)
                    if target is not None:
                        path = bfs(self.walls, self.hunter.r, self.hunter.c,
                                   target["r"], target["c"], self.gates)
                        if path:
                            self.hunter.r, self.hunter.c = path[0]
                        for p in self.mp_players:
                            if p["alive"] and (p["r"], p["c"]) == (self.hunter.r, self.hunter.c):
                                p["alive"] = False
            # maze shift
            if self.mp_settings.get("maze_shift", True):
                self.maze_timer += dt
                if self.maze_timer >= MAZE_CHANGE_INTERVAL:
                    self.maze_timer = 0.0
                    self.host_shift_maze()

        else:  # DBD mode
            self._dbd_tick(dt)
            if self.state == "mp_end":
                return

        # 6. clear pending inputs
        self.mp_pending_input = {}

        # 7. broadcast state ~30Hz
        self.mp_broadcast_timer += dt
        if self.mp_broadcast_timer >= 1.0 / 30.0:
            self.mp_broadcast_timer = 0.0
            self.server.broadcast(self._serialize_state())

    # ── DBD core logic ────────────────────────────────────────────────────────
    def _dbd_tick(self, dt):
        """Generators, catches, imprisonment, rescues, win conditions."""
        # match timer
        self.mp_match_timer -= dt
        if self.mp_match_timer <= 0:
            self.mp_match_timer = 0
            self._end_dbd("hunter")
            return

        # generators: any adjacent runner holding E ticks progress
        for gen in self.mp_generators:
            if gen.completed:
                continue
            ticking = False
            for p in self.mp_players:
                if p["role"] != "runner" or not p["alive"] or p.get("imprisoned"):
                    continue
                if gen.is_adjacent(p["r"], p["c"]):
                    inp = self.mp_pending_input.get(p["id"], {})
                    if inp.get("e_held"):
                        ticking = True
                        break
            if ticking:
                gen.progress += dt
                if gen.progress >= GEN_REPAIR_TIME:
                    gen.progress = GEN_REPAIR_TIME
                    gen.completed = True

        # all gens done → unlock exit
        if not self.exit_unlocked and all(g.completed for g in self.mp_generators):
            self.exit_unlocked = True

        # hunter catches: hunter shares cell with a runner → imprison
        hunter = next((p for p in self.mp_players if p["role"] == "hunter"), None)
        if hunter and hunter["alive"]:
            for p in self.mp_players:
                if p["role"] != "runner" or not p["alive"] or p.get("imprisoned"):
                    continue
                if (p["r"], p["c"]) == (hunter["r"], hunter["c"]):
                    self._imprison_runner(p)

        # rescue: any free runner adjacent to a warehouse holding an imprisoned one
        for w in self.mp_warehouses:
            if w.imprisoned_pid is None:
                continue
            for p in self.mp_players:
                if p["role"] != "runner" or not p["alive"] or p.get("imprisoned"):
                    continue
                if w.is_adjacent(p["r"], p["c"]):
                    inp = self.mp_pending_input.get(p["id"], {})
                    if inp.get("e_held"):
                        self._free_runner(w)
                        break

        # imprisonment timers
        for p in self.mp_players:
            if p.get("imprisoned"):
                p["imprison_remaining"] -= dt
                if p["imprison_remaining"] <= 0:
                    # timer ran out → eliminated
                    p["alive"] = False
                    p["imprisoned"] = False
                    for w in self.mp_warehouses:
                        if w.imprisoned_pid == p["id"]:
                            w.imprisoned_pid = None

        # win check: all runners eliminated → hunter wins
        runners = [p for p in self.mp_players if p["role"] == "runner"]
        if runners and all(not p["alive"] for p in runners):
            self._end_dbd("hunter")
            return

    def _imprison_runner(self, runner):
        # find a warehouse without an imprisoned player
        free_wh = [w for w in self.mp_warehouses if w.imprisoned_pid is None]
        if not free_wh:
            # all warehouses occupied — just kill them (edge case)
            runner["alive"] = False
            return
        w = random.choice(free_wh)
        runner["r"], runner["c"] = w.r, w.c
        runner["imprisoned"] = True
        runner["imprison_remaining"] = WAREHOUSE_IMPRISON
        w.imprisoned_pid = runner["id"]

    def _free_runner(self, w):
        for p in self.mp_players:
            if p["id"] == w.imprisoned_pid:
                p["imprisoned"] = False
                p["imprison_remaining"] = 0.0
                # bump them to an adjacent free cell so they aren't stuck on the warehouse
                for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                    nr, nc = w.r + dr, w.c + dc
                    if not is_wall(self.walls, nr, nc, self.gates):
                        p["r"], p["c"] = nr, nc
                        break
                break
        w.imprisoned_pid = None

    def _end_dbd(self, winner):
        self.mp_winner = winner
        self.state = "mp_end"
        # final state push so clients see correct positions / timer
        if self.server is not None:
            self.server.broadcast(self._serialize_state())
            self.server.broadcast({"type": "end", "winner": winner})

    def _nearest_alive_to(self, r, c):
        best = None
        bestd = 10 ** 9
        for p in self.mp_players:
            if not p["alive"]:
                continue
            d = abs(p["r"] - r) + abs(p["c"] - c)
            if d < bestd:
                bestd, best = d, p
        return best

    def host_advance_level(self):
        """Host-side: build next level, reset positions, broadcast new maze."""
        self.new_level()
        # re-apply ESCAPE settings (new_level rebuilds defaults)
        if not self.mp_settings.get("portals", True):
            self.portals = []
        if self.mp_settings.get("hunter", "bot") == "off":
            self.hunter = None
        spawns = [(1, 1), (1, 3), (3, 1), (3, 3)]
        for i, p in enumerate(self.mp_players):
            r, c = spawns[i] if i < len(spawns) else (1, 1)
            r, c = nearest_free(self.walls, r, c, self.gates)
            p["r"], p["c"] = r, c
            p["alive"] = True
        self.server.broadcast(self._serialize_maze())

    def host_shift_maze(self):
        """Maze shift in MP: regenerate, snap all players, broadcast new maze."""
        self.walls = build_maze(ROWS, COLS)
        self.gates = []
        self.build_gates()
        self.portals = []
        if self.mp_settings.get("portals", True):
            self.build_portals()

        for p in self.mp_players:
            if p["alive"]:
                p["r"], p["c"] = nearest_free(self.walls, p["r"], p["c"], self.gates)
        if self.hunter is not None:
            self.hunter.r, self.hunter.c = nearest_free(
                self.walls, self.hunter.r, self.hunter.c, self.gates
            )
        self.server.broadcast(self._serialize_maze())

    # ── client: per-frame tick ────────────────────────────────────────────────
    def mp_client_tick(self, dt):
        """Drain server messages; send our input throttled."""
        for msg in self.client.drain():
            t = msg.get("type")
            if t == "welcome":
                self.player_id = msg.get("id", 0)
            elif t == "maze":
                self._apply_maze(msg)
            elif t == "state":
                self._apply_state(msg)
            elif t == "start":
                self.mp_mode   = msg.get("mode", "escape")
                self.mp_winner = ""
                if self.state in ("lobby_wait_client", "mp_end"):
                    self.state = "mp_play"
            elif t == "end":
                self.mp_winner = msg.get("winner", "")
                self.state = "mp_end"

        if not self.client.alive:
            self.mp_status_msg = "Lost connection to host."
            self.mp_disconnect()
            return

        # send input ~30Hz
        self.mp_send_timer += dt
        if self.mp_send_timer >= 1.0 / 30.0:
            self.mp_send_timer = 0.0
            self.client.send({"type": "input", **self.mp_local_input})

    # ── multiplayer render ────────────────────────────────────────────────────
    def draw_mp_play(self):
        self.draw_maze()

        for g in self.gates:
            g.draw(self.surf, self.walls)

        # DBD entities
        for gen in self.mp_generators:
            gen.draw(self.surf)
        for w in self.mp_warehouses:
            w.draw(self.surf)

        # portals (escape mode only)
        if self.mp_mode == "escape":
            t = pygame.time.get_ticks() / 1000
            for p in self.portals:
                p.draw(self.surf, t)

        # exit (X-overlay if locked)
        self.draw_exit()
        if not self.exit_unlocked:
            ex, ey = cell_xy(*self.exit_pos)
            pygame.draw.line(self.surf, (255, 80, 80),
                             (ex - CELL // 3, ey - CELL // 3),
                             (ex + CELL // 3, ey + CELL // 3), 3)
            pygame.draw.line(self.surf, (255, 80, 80),
                             (ex - CELL // 3, ey + CELL // 3),
                             (ex + CELL // 3, ey - CELL // 3), 3)

        # players
        for p in self.mp_players:
            if not p["alive"]:
                continue
            # any player with role=hunter (DBD or ESCAPE-with-player-hunter) is red
            if p.get("role") == "hunter":
                color = (255, 60, 60)
            else:
                color = PLAYER_COLORS[p["id"] % len(PLAYER_COLORS)]
            x, y   = cell_xy(p["r"], p["c"])
            radius = int(CELL * 0.30)

            # imprisoned runners: draw smaller + a circle outline
            if p.get("imprisoned"):
                pygame.draw.circle(self.surf, color, (x, y), radius // 2)
                pygame.draw.circle(self.surf, (200, 200, 200), (x, y), radius, 2)
                # remaining-time arc
                frac = max(0.0, p.get("imprison_remaining", 0)) / WAREHOUSE_IMPRISON
                arc_rect = pygame.Rect(x - radius - 4, y - radius - 4,
                                       (radius + 4) * 2, (radius + 4) * 2)
                pygame.draw.arc(self.surf, (255, 80, 80), arc_rect,
                                -math.pi / 2,
                                -math.pi / 2 + math.tau * frac, 3)
            else:
                glow = pygame.Surface((radius * 4, radius * 4), pygame.SRCALPHA)
                pygame.draw.circle(glow, (*color, 60),
                                   (radius * 2, radius * 2), radius * 2)
                self.surf.blit(glow, (x - radius * 2, y - radius * 2))
                pygame.draw.circle(self.surf, color, (x, y), radius)

            # own avatar: white outline
            if p["id"] == self.player_id:
                pygame.draw.circle(self.surf, WHITE, (x, y), radius + 2, 2)

        # bot hunter (escape mode only)
        if self.mp_mode == "escape" and self.hunter is not None:
            self.hunter.draw(self.surf)

        self.draw_mp_panel()

    def draw_mp_panel(self):
        pygame.draw.rect(self.surf, PANEL_BG, (PANEL_X, 0, PANEL_W, H))
        pygame.draw.line(self.surf, (0, 120, 200), (PANEL_X, 0), (PANEL_X, H), 3)

        mode_label = "ESCAPE" if self.mp_mode == "escape" else "DBD"
        role = "HOST" if self.server is not None else "CLIENT"
        title = self.font_lg.render(mode_label, True, CYAN)
        self.surf.blit(title, (PANEL_X + PANEL_W // 2 - title.get_width() // 2, 18))

        if self.mp_mode == "escape":
            sub = self.font_sm.render(f"{role}  -  LEVEL {self.level}",
                                      True, (160, 160, 160))
            self.surf.blit(sub, (PANEL_X + PANEL_W // 2 - sub.get_width() // 2, 70))
        else:
            mins = max(0, int(self.mp_match_timer)) // 60
            secs = max(0, int(self.mp_match_timer)) % 60
            time_txt = self.font_lg.render(f"{mins:01d}:{secs:02d}",
                                           True, (255, 220, 80))
            self.surf.blit(time_txt,
                           (PANEL_X + PANEL_W // 2 - time_txt.get_width() // 2, 60))
            done = sum(1 for g in self.mp_generators if g.completed)
            gen_txt = self.font_med.render(
                f"GENERATORS  {done} / {len(self.mp_generators)}",
                True, WHITE)
            self.surf.blit(gen_txt,
                           (PANEL_X + PANEL_W // 2 - gen_txt.get_width() // 2, 110))
            status = "EXIT UNLOCKED" if self.exit_unlocked else "EXIT LOCKED"
            scol = (80, 255, 80) if self.exit_unlocked else (255, 80, 80)
            stxt = self.font_sm.render(status, True, scol)
            self.surf.blit(stxt,
                           (PANEL_X + PANEL_W // 2 - stxt.get_width() // 2, 152))

        # player list
        y0 = 200
        head = self.font_med.render("PLAYERS", True, WHITE)
        self.surf.blit(head, (PANEL_X + 24, y0))

        for i, p in enumerate(self.mp_players):
            if p.get("role") == "hunter":
                color    = (255, 60, 60)
                role_tag = " [H]"
            else:
                color    = PLAYER_COLORS[p["id"] % len(PLAYER_COLORS)]
                role_tag = ""
            label = f"P{p['id'] + 1}{role_tag}" + \
                    ("  (you)" if p["id"] == self.player_id else "")
            if not p["alive"]:
                status = "ELIMINATED"
            elif p.get("imprisoned"):
                status = f"IMPRISONED {p['imprison_remaining']:.0f}s"
            else:
                status = "ALIVE"
            text = self.font_sm.render(f"{label}  -  {status}", True, color)
            pygame.draw.rect(self.surf, color, (PANEL_X + 24, y0 + 50 + i * 36, 14, 14))
            self.surf.blit(text, (PANEL_X + 46, y0 + 46 + i * 36))

        hint_y = H - 220
        if self.mp_mode == "escape":
            hints = [
                "WASD / ARROWS : MOVE",
                "HOLD E : open gate",
                "ESC : leave match",
            ]
        else:
            hints = [
                "WASD / ARROWS : MOVE",
                "HOLD E : repair / rescue / gate",
                "ESC : leave match",
            ]
        for i, hint in enumerate(hints):
            txt = self.font_sm.render(hint, True, (160, 160, 160))
            self.surf.blit(txt, (PANEL_X + 24, hint_y + i * 28))

    def draw_mp_end(self):
        # render the maze + entities frozen behind the overlay
        self.draw_mp_play()

        if self.mp_winner == "runners":
            title  = "RUNNERS WIN"
            color  = (80, 255, 80)
            reason = "A runner reached the exit."
        elif self.mp_winner == "hunter":
            title  = "HUNTER WINS"
            color  = (255, 80, 80)
            reason = "Time ran out or all runners eliminated."
        else:
            title  = "MATCH OVER"
            color  = WHITE
            reason = ""

        # overlay box (bigger than the generic draw_overlay because we need buttons)
        ow, oh = 760, 380
        ox = MAZE_OX + MAZE_W // 2 - ow // 2
        oy = MAZE_OY + MAZE_H // 2 - oh // 2
        panel = pygame.Surface((ow, oh), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 220))
        self.surf.blit(panel, (ox, oy))
        pygame.draw.rect(self.surf, color, (ox, oy, ow, oh), 3)

        ts = self.font_xl.render(title, True, color)
        self.surf.blit(ts, (ox + ow // 2 - ts.get_width() // 2, oy + 36))

        if reason:
            sub = self.font_med.render(reason, True, WHITE)
            self.surf.blit(sub, (ox + ow // 2 - sub.get_width() // 2, oy + 140))

        # buttons
        self.mp_end_rects = {}
        btn_w, btn_h = 240, 64
        btn_y = oy + oh - btn_h - 50

        if self.server is not None:
            # host: REPLAY + MENU side by side
            gap = 30
            bx_replay = ox + ow // 2 - btn_w - gap // 2
            bx_menu   = ox + ow // 2 + gap // 2

            replay_rect = pygame.Rect(bx_replay, btn_y, btn_w, btn_h)
            pygame.draw.rect(self.surf, (0, 80, 40), replay_rect, border_radius=6)
            pygame.draw.rect(self.surf, (80, 255, 80), replay_rect, 2, border_radius=6)
            rt = self.font_med.render("REPLAY", True, WHITE)
            self.surf.blit(rt, (replay_rect.centerx - rt.get_width() // 2,
                                replay_rect.centery - rt.get_height() // 2))
            self.mp_end_rects["replay"] = replay_rect

            menu_rect = pygame.Rect(bx_menu, btn_y, btn_w, btn_h)
            pygame.draw.rect(self.surf, (40, 40, 60), menu_rect, border_radius=6)
            pygame.draw.rect(self.surf, WHITE, menu_rect, 2, border_radius=6)
            mt = self.font_med.render("MENU", True, WHITE)
            self.surf.blit(mt, (menu_rect.centerx - mt.get_width() // 2,
                                menu_rect.centery - mt.get_height() // 2))
            self.mp_end_rects["menu"] = menu_rect

            hint = self.font_sm.render(
                "ENTER = replay  -  ESC = menu", True, (160, 160, 160))
            self.surf.blit(hint,
                           (ox + ow // 2 - hint.get_width() // 2, btn_y + btn_h + 10))
        else:
            # client: only MENU; show wait line
            wait = self.font_sm.render(
                "Waiting for host... press REPLAY to play again.",
                True, (180, 180, 180))
            self.surf.blit(wait,
                           (ox + ow // 2 - wait.get_width() // 2, btn_y - 28))

            menu_rect = pygame.Rect(ox + ow // 2 - btn_w // 2, btn_y, btn_w, btn_h)
            pygame.draw.rect(self.surf, (40, 40, 60), menu_rect, border_radius=6)
            pygame.draw.rect(self.surf, WHITE, menu_rect, 2, border_radius=6)
            mt = self.font_med.render("MENU", True, WHITE)
            self.surf.blit(mt, (menu_rect.centerx - mt.get_width() // 2,
                                menu_rect.centery - mt.get_height() // 2))
            self.mp_end_rects["menu"] = menu_rect

            hint = self.font_sm.render("ESC = menu", True, (160, 160, 160))
            self.surf.blit(hint,
                           (ox + ow // 2 - hint.get_width() // 2, btn_y + btn_h + 10))

    def host_replay(self):
        """Host restarts the match with current mode + settings."""
        if self.server is None:
            return
        self.host_start_match()

    # ── main loop ─────────────────────────────────────────────────────────────
    def run(self):
        move_keys = {
            pygame.K_UP:    (-1,  0),
            pygame.K_w:     (-1,  0),
            pygame.K_DOWN:  ( 1,  0),
            pygame.K_s:     ( 1,  0),
            pygame.K_LEFT:  ( 0, -1),
            pygame.K_a:     ( 0, -1),
            pygame.K_RIGHT: ( 0,  1),
            pygame.K_d:     ( 0,  1),
        }

        while True:
            dt = self.clock.tick(FPS) / 1000.0

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        if self.state == "menu":
                            pygame.quit()
                            sys.exit()
                        elif self.state in ("lobby_wait_host", "lobby_wait_client",
                                            "mp_play", "mp_end"):
                            self.mp_disconnect()
                        elif self.state == "lobby_join_input":
                            self.state = "lobby"
                        elif self.state == "lobby_mode_pick":
                            self.state = "lobby"
                        elif self.state == "lobby":
                            self.state = "menu"
                        else:
                            self.state = "menu"

                    elif self.state == "menu":
                        if event.key in (pygame.K_UP, pygame.K_w):
                            self.menu_index = (self.menu_index - 1) % len(self.menu_options)
                        elif event.key in (pygame.K_DOWN, pygame.K_s):
                            self.menu_index = (self.menu_index + 1) % len(self.menu_options)
                        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            self.select_menu_option()

                    elif self.state == "lobby":
                        if event.key in (pygame.K_UP, pygame.K_w):
                            self.lobby_index = (self.lobby_index - 1) % len(self.lobby_options)
                        elif event.key in (pygame.K_DOWN, pygame.K_s):
                            self.lobby_index = (self.lobby_index + 1) % len(self.lobby_options)
                        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            self.select_lobby_option()

                    elif self.state == "lobby_mode_pick":
                        if event.key in (pygame.K_UP, pygame.K_w):
                            self.mode_index = (self.mode_index - 1) % len(self.mode_options)
                        elif event.key in (pygame.K_DOWN, pygame.K_s):
                            self.mode_index = (self.mode_index + 1) % len(self.mode_options)
                        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            self.select_mode_option()

                    elif self.state == "lobby_join_input":
                        if event.key == pygame.K_RETURN:
                            if self.mp_text_input.strip():
                                self.start_client_mode(self.mp_text_input)
                        elif event.key == pygame.K_BACKSPACE:
                            self.mp_text_input = self.mp_text_input[:-1]
                        else:
                            ch = event.unicode
                            if ch and (ch.isdigit() or ch in ".:" or ch.isalpha() or ch == "-"):
                                if len(self.mp_text_input) < 40:
                                    self.mp_text_input += ch

                    elif self.state == "mp_end":
                        if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            if self.server is not None:
                                self.host_replay()

                    elif self.state == "lobby_wait_host":
                        if event.key == pygame.K_SPACE:
                            self.host_start_match()
                        elif self.mp_mode == "escape":
                            if event.key in (pygame.K_UP, pygame.K_w):
                                self.settings_index = \
                                    (self.settings_index - 1) % len(self.settings_rows)
                            elif event.key in (pygame.K_DOWN, pygame.K_s):
                                self.settings_index = \
                                    (self.settings_index + 1) % len(self.settings_rows)
                            elif event.key in (pygame.K_LEFT, pygame.K_a):
                                key = self.settings_rows[self.settings_index] \
                                          .lower().replace(" ", "_")
                                self.cycle_setting(key, -1)
                            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                                key = self.settings_rows[self.settings_index] \
                                          .lower().replace(" ", "_")
                                self.cycle_setting(key, +1)

                    elif self.state == "title":
                        self.state = "play"
                    elif self.state == "dead":
                        self.reset()
                        self.state = "play"

                # mouse clicks
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if self.state == "menu":
                        self.handle_menu_click(event.pos)
                    elif self.state == "lobby":
                        self.handle_lobby_click(event.pos)
                    elif self.state == "lobby_mode_pick":
                        self.handle_mode_click(event.pos)
                    elif self.state == "mp_end":
                        if "replay" in self.mp_end_rects \
                           and self.mp_end_rects["replay"].collidepoint(event.pos):
                            self.host_replay()
                        elif "menu" in self.mp_end_rects \
                             and self.mp_end_rects["menu"].collidepoint(event.pos):
                            self.mp_disconnect()
                    elif self.state == "play":
                        # panel toggle buttons
                        self.btn_maze.handle_click(event.pos)
                        self.btn_hunter.handle_click(event.pos)
                        self.btn_portals.handle_click(event.pos)

            if self.state == "play":
                pressed = pygame.key.get_pressed()
                e_held  = pressed[pygame.K_e]

                # gates (hold E to toggle; gate stays in toggled state)
                self.update_gates(dt, e_held)

                # player movement
                self.move_timer += dt
                if self.move_timer >= MOVE_DELAY:
                    for key, (dr, dc) in move_keys.items():
                        if pressed[key]:
                            self.move_timer = 0
                            self.try_move(dr, dc)
                            break

                # hunter
                self.update_hunter(dt)

                # maze shift
                if self.btn_maze.state:
                    self.maze_timer += dt
                    if self.maze_timer >= MAZE_CHANGE_INTERVAL:
                        self.maze_timer = 0.0
                        self.shift_maze()

            elif self.state == "lobby_wait_host" and self.server is not None:
                # accept hellos, send welcomes
                for ci, msg in self.server.drain_all():
                    if msg.get("type") == "hello":
                        self.server.send_to(ci, {"type": "welcome", "id": ci + 1})
                self.server.prune_dead()

            elif self.state == "lobby_wait_client" and self.client is not None:
                self.mp_client_tick(dt)

            elif self.state == "mp_end":
                # keep the client draining so it sees the host's REPLAY
                if self.client is not None:
                    self.mp_client_tick(dt)

            elif self.state == "mp_play":
                # capture local input (used by host as P0, or sent by client)
                pressed = pygame.key.get_pressed()
                # priority: up > down > left > right (matches single-player feel)
                if pressed[pygame.K_UP] or pressed[pygame.K_w]:
                    dr, dc = -1, 0
                elif pressed[pygame.K_DOWN] or pressed[pygame.K_s]:
                    dr, dc = 1, 0
                elif pressed[pygame.K_LEFT] or pressed[pygame.K_a]:
                    dr, dc = 0, -1
                elif pressed[pygame.K_RIGHT] or pressed[pygame.K_d]:
                    dr, dc = 0, 1
                else:
                    dr, dc = 0, 0
                self.mp_local_input = {
                    "dr": dr, "dc": dc, "e_held": pressed[pygame.K_e],
                }

                if self.server is not None:
                    self.mp_host_tick(dt)
                elif self.client is not None:
                    self.mp_client_tick(dt)

            # ── render ────────────────────────────────────────────────────────
            if self.state == "menu":
                self.draw_menu()

            elif self.state == "lobby":
                self.draw_lobby()

            elif self.state == "lobby_mode_pick":
                self.draw_lobby_mode_pick()

            elif self.state == "lobby_join_input":
                self.draw_lobby_join_input()

            elif self.state in ("lobby_wait_host", "lobby_wait_client"):
                self.draw_lobby_wait()

            elif self.state == "mp_play":
                self.draw_mp_play()

            elif self.state == "mp_end":
                self.draw_mp_end()

            else:
                self.draw_maze()

                for g in self.gates:
                    g.draw(self.surf, self.walls)

                if self.btn_portals.state:
                    t = pygame.time.get_ticks() / 1000
                    for p in self.portals:
                        p.draw(self.surf, t)

                self.draw_exit()
                self.player.draw(self.surf)

                if self.btn_hunter.state:
                    self.hunter.draw(self.surf)

                self.draw_panel()
                self.draw_maze_timer_bar()

                if self.state == "title":
                    self.draw_overlay(
                        "VOID MAZE",
                        [
                            "WASD / ARROWS : MOVE",
                            "HOLD E : OPEN NEARBY GATE",
                            "AVOID THE RED HUNTER",
                            "REACH THE EXIT",
                        ],
                        CYAN,
                    )

                elif self.state == "dead":
                    self.draw_overlay(
                        "YOU DIED",
                        [
                            f"SCORE : {self.score}",
                            f"LEVEL : {self.level}",
                            "",
                            "PRESS ANY KEY",
                        ],
                        (255, 70, 70),
                    )

            pygame.display.flip()


if __name__ == "__main__":
    Game().run()