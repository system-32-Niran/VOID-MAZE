"""
VOID MAZE — v1.3
Add gate (open by hold E), add portal, maze shift (random)
"""

import pygame
import math
import random
import sys
from collections import deque

pygame.init()

# ── layout ────────────────────────────────────────────────────────────────────
DISPLAY = pygame.display.Info()
SCREEN_W = DISPLAY.current_w
SCREEN_H = DISPLAY.current_h

_CELL = 32
COLS = (SCREEN_W // _CELL)
COLS = COLS if COLS % 2 == 1 else COLS - 1
ROWS = (SCREEN_H // _CELL)
ROWS = ROWS if ROWS % 2 == 1 else ROWS - 1

CELL = min(SCREEN_W // COLS, SCREEN_H // ROWS)
W = COLS * CELL
H = ROWS * CELL

FPS                  = 60
MOVE_DELAY           = 0.11
HUNTER_DELAY         = 0.38
MAZE_CHANGE_INTERVAL = 20.0
GATE_COUNT           = 6
GATE_OPEN_TIME       = 1.2

BLACK             = (0, 0, 0)
CYAN              = (0, 255, 200)
WHITE             = (255, 255, 255)
GATE_COLOR_CLOSED = (180, 120, 0)
GATE_COLOR_OPEN   = (80, 255, 80)

PORTAL_COLORS = [
    (0, 200, 255), (255, 160, 0), (80, 255, 80),
    (255, 80, 255), (255, 220, 0), (80, 80, 255),
]


# ── maze generation ───────────────────────────────────────────────────────────
def build_maze(rows, cols):
    walls   = [[True]  * cols for _ in range(rows)]
    visited = [[False] * cols for _ in range(rows)]
    stack   = [(1, 1)]
    visited[1][1] = True
    walls[1][1]   = False
    dirs = [(0, 2), (2, 0), (0, -2), (-2, 0)]
    while stack:
        r, c = stack[-1]
        random.shuffle(dirs)
        moved = False
        for dr, dc in dirs:
            nr, nc = r + dr, c + dc
            if 0 < nr < rows - 1 and 0 < nc < cols - 1 and not visited[nr][nc]:
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
    if r < 0 or r >= ROWS or c < 0 or c >= COLS:
        return True
    if walls[r][c]:
        if gates:
            for g in gates:
                if g.r == r and g.c == c and g.is_open:
                    return False
        return True
    return False


def nearest_free(walls, r, c, gates=None):
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
    return r, c


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
            if 0 <= nr < ROWS and 0 <= nc < COLS and not is_wall(walls, nr, nc, gates) and dist[nr][nc] == -1:
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


def free_cell(walls, exclude=None):
    exclude = set(exclude or [])
    for _ in range(1000):
        r = random.choice(range(1, ROWS - 1, 2))
        c = random.choice(range(1, COLS - 1, 2))
        if not walls[r][c] and (r, c) not in exclude:
            return r, c
    return None


def cell_xy(r, c):
    return (c * CELL + CELL // 2, r * CELL + CELL // 2)


# ── gate ──────────────────────────────────────────────────────────────────────
class Gate:
    def __init__(self, r, c):
        self.r = r
        self.c = c
        self.is_open   = False
        self._progress = {}

    def is_adjacent(self, r, c):
        return abs(r - self.r) + abs(c - self.c) == 1

    def update(self, dt, entity_id, pressing):
        prev = self._progress.get(entity_id, 0.0)
        self._progress[entity_id] = min(GATE_OPEN_TIME, prev + dt) if pressing else 0.0
        was_open  = self.is_open
        self.is_open = any(v >= GATE_OPEN_TIME for v in self._progress.values())
        return (not was_open) and self.is_open

    def close(self):
        self._progress = {}
        self.is_open   = False

    def player_progress(self):
        return self._progress.get("player", 0.0)

    def draw(self, surf):
        x, y  = cell_xy(self.r, self.c)
        color = GATE_COLOR_OPEN if self.is_open else GATE_COLOR_CLOSED
        bar_w = CELL - 4
        bar_h = max(4, CELL // 5)
        rect  = pygame.Rect(x - bar_w // 2, y - bar_h // 2, bar_w, bar_h)
        pygame.draw.rect(surf, color, rect, border_radius=3)

        prog = self.player_progress()
        if prog > 0 and not self.is_open:
            frac = prog / GATE_OPEN_TIME
            arc_rect = pygame.Rect(x - CELL//2 + 2, y - CELL//2 + 2, CELL - 4, CELL - 4)
            pygame.draw.arc(surf, (255, 220, 0), arc_rect, math.pi / 2, math.pi / 2 + math.tau * frac, 3)

        if self.is_open:
            s = pygame.Surface((CELL, CELL), pygame.SRCALPHA)
            s.fill((80, 255, 80, 40))
            surf.blit(s, (self.c * CELL, self.r * CELL))


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
            pygame.draw.circle(glow, (*self.color, int(40 * ring * pulse)), (radius + 2, radius + 2), radius, 2)
            surf.blit(glow, (x - radius - 2, y - radius - 2))
        pygame.draw.circle(surf, self.color, (x, y), max(6, size // 2))
        pygame.draw.circle(surf, WHITE,      (x, y), max(3, size // 5))


# ── player ────────────────────────────────────────────────────────────────────
class Player:
    def __init__(self, r, c):
        self.r = r
        self.c = c
        self.trail = deque(maxlen=16)

    def draw(self, surf):
        for i, (tr, tc) in enumerate(self.trail):
            frac = i / max(1, len(self.trail))
            size = max(2, int(CELL * 0.12 * frac))
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


# ── hunter ────────────────────────────────────────────────────────────────────
class Hunter:
    def __init__(self, r, c):
        self.r = r
        self.c = c
        self.trail = deque(maxlen=12)

    def draw(self, surf):
        for i, (tr, tc) in enumerate(self.trail):
            frac = i / max(1, len(self.trail))
            size = max(2, int(CELL * 0.11 * frac))
            tx, ty = cell_xy(tr, tc)
            trail = pygame.Surface((size * 2, size * 2), pygame.SRCALPHA)
            pygame.draw.circle(trail, (255, 60, 60, int(90 * frac)), (size, size), size)
            surf.blit(trail, (tx - size, ty - size))
        x, y = cell_xy(self.r, self.c)
        pygame.draw.circle(surf, (255, 60, 60), (x, y), int(CELL * 0.30))


# ── game ──────────────────────────────────────────────────────────────────────
class Game:
    def __init__(self):
        self.surf  = pygame.display.set_mode((W, H))
        pygame.display.set_caption("VOID MAZE v1.3")
        self.clock = pygame.time.Clock()
        self.font_xl  = pygame.font.Font(None, 72)
        self.font_med = pygame.font.Font(None, 40)
        self.font_sm  = pygame.font.Font(None, 30)
        self.state = "title"
        self.reset()

    def reset(self):
        self.level        = 1
        self.score        = 0
        self.portal_count = 0
        self.new_level()

    def new_level(self):
        self.walls    = build_maze(ROWS, COLS)
        self.player   = Player(1, 1)
        self.hunter   = Hunter(ROWS - 2, COLS - 2)
        self.exit_pos = (ROWS - 2, COLS - 2)
        self.portals  = []
        self.gates    = []
        self.move_timer   = 0.0
        self.hunter_timer = 0.0
        self.maze_timer   = 0.0
        self.build_portals()
        self.build_gates()

    def build_portals(self):
        taken = [(1, 1), self.exit_pos]
        for i in range(6):
            a = free_cell(self.walls, taken)
            if a is None: break
            taken.append(a)
            b = free_cell(self.walls, taken)
            if b is None: break
            taken.append(b)
            idx   = len(self.portals)
            color = PORTAL_COLORS[i % len(PORTAL_COLORS)]
            self.portals.append(Portal(a[0], a[1], idx + 1, color))
            self.portals.append(Portal(b[0], b[1], idx,     color))

    def build_gates(self):
        candidates = [
            (r, c)
            for r in range(1, ROWS - 1)
            for c in range(1, COLS - 1)
            if self.walls[r][c]
        ]
        random.shuffle(candidates)
        count = 0
        for r, c in candidates:
            if count >= GATE_COUNT:
                break
            free_nb = [(r+dr, c+dc) for dr, dc in ((0,1),(1,0),(0,-1),(-1,0))
                       if not is_wall(self.walls, r+dr, c+dc)]
            if len(free_nb) >= 2:
                self.gates.append(Gate(r, c))
                count += 1

    def shift_maze(self):
        pr, pc = self.player.r, self.player.c
        hr, hc = self.hunter.r, self.hunter.c
        self.walls   = build_maze(ROWS, COLS)
        self.gates   = []
        self.portals = []
        self.build_gates()
        self.build_portals()
        self.player.r, self.player.c = nearest_free(self.walls, pr, pc, self.gates)
        self.hunter.r, self.hunter.c = nearest_free(self.walls, hr, hc, self.gates)
        self.player.trail.clear()
        self.hunter.trail.clear()

    def update_gates(self, dt, e_pressed):
        for gate in self.gates:
            player_adj = gate.is_adjacent(self.player.r, self.player.c)
            gate.update(dt, "player", e_pressed and player_adj)
            hunter_adj = gate.is_adjacent(self.hunter.r, self.hunter.c)
            gate.update(dt, "hunter", hunter_adj)

    def update_hunter(self, dt):
        self.hunter_timer += dt
        if self.hunter_timer < HUNTER_DELAY:
            return
        self.hunter_timer = 0
        path = bfs(self.walls, self.hunter.r, self.hunter.c,
                   self.player.r, self.player.c, self.gates)
        if path:
            self.hunter.trail.append((self.hunter.r, self.hunter.c))
            self.hunter.r, self.hunter.c = path[0]
        if (self.player.r, self.player.c) == (self.hunter.r, self.hunter.c):
            self.state = "dead"

    def try_move(self, dr, dc):
        nr = self.player.r + dr
        nc = self.player.c + dc
        if is_wall(self.walls, nr, nc, self.gates):
            return
        self.player.trail.append((self.player.r, self.player.c))
        self.player.r = nr
        self.player.c = nc
        self.score += 1
        self.check_portal()
        self.check_exit()
        self.check_caught()

    def check_portal(self):
        for p in self.portals:
            if (p.r, p.c) == (self.player.r, self.player.c):
                dest = self.portals[p.pair_idx]
                self.player.r = dest.r
                self.player.c = dest.c
                self.score        += 15
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

    def draw_maze(self):
        self.surf.fill(BLACK)
        gate_cells = {(g.r, g.c) for g in self.gates}
        for r in range(ROWS):
            for c in range(COLS):
                if self.walls[r][c] and (r, c) not in gate_cells:
                    rect = pygame.Rect(c * CELL, r * CELL, CELL, CELL)
                    pygame.draw.rect(self.surf, (0, 40, 70),  rect)
                    pygame.draw.rect(self.surf, (0, 90, 140), rect, 1)

    def draw_exit(self):
        er, ec = self.exit_pos
        x, y   = cell_xy(er, ec)
        size   = int(CELL * 0.35)
        points = [(x, y - size), (x + size, y), (x, y + size), (x - size, y)]
        pygame.draw.polygon(self.surf, WHITE, points)

    def draw_maze_timer_bar(self):
        frac  = self.maze_timer / MAZE_CHANGE_INTERVAL
        bar_w = int(W * frac)
        pygame.draw.rect(self.surf, (0, 60, 100),  (0, 0, W, 4))
        pygame.draw.rect(self.surf, (0, 200, 255), (0, 0, bar_w, 4))

    def draw_hud(self):
        secs  = max(0, MAZE_CHANGE_INTERVAL - self.maze_timer)
        text  = f"LEVEL {self.level}   SCORE {self.score}   PORTALS {self.portal_count}   SHIFT {secs:.1f}s"
        lv    = self.font_sm.render(text, True, CYAN)
        self.surf.blit(lv, (12, 10))

    def draw_overlay(self, title, lines, color):
        ow, oh = 700, 320
        ox = W // 2 - ow // 2
        oy = H // 2 - oh // 2
        overlay = pygame.Surface((ow, oh), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 220))
        self.surf.blit(overlay, (ox, oy))
        pygame.draw.rect(self.surf, color, (ox, oy, ow, oh), 3)
        ts = self.font_xl.render(title, True, color)
        self.surf.blit(ts, (ox + ow // 2 - ts.get_width() // 2, oy + 30))
        for i, line in enumerate(lines):
            ls = self.font_med.render(line, True, WHITE)
            self.surf.blit(ls, (ox + ow // 2 - ls.get_width() // 2, oy + 120 + i * 40))

    def run(self):
        move_keys = {
            pygame.K_UP:    (-1,  0), pygame.K_w:     (-1,  0),
            pygame.K_DOWN:  ( 1,  0), pygame.K_s:     ( 1,  0),
            pygame.K_LEFT:  ( 0, -1), pygame.K_a:     ( 0, -1),
            pygame.K_RIGHT: ( 0,  1), pygame.K_d:     ( 0,  1),
        }

        while True:
            dt = self.clock.tick(FPS) / 1000.0

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit(); sys.exit()
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        pygame.quit(); sys.exit()
                    if self.state == "title":
                        self.state = "play"
                    elif self.state == "dead":
                        self.reset()
                        self.state = "play"

            if self.state == "play":
                pressed = pygame.key.get_pressed()
                e_held  = pressed[pygame.K_e]

                self.move_timer += dt
                if self.move_timer >= MOVE_DELAY:
                    for key, (dr, dc) in move_keys.items():
                        if pressed[key]:
                            self.move_timer = 0
                            self.try_move(dr, dc)
                            break

                self.update_gates(dt, e_held)
                self.update_hunter(dt)

                self.maze_timer += dt
                if self.maze_timer >= MAZE_CHANGE_INTERVAL:
                    self.maze_timer = 0.0
                    self.shift_maze()

            self.draw_maze()
            for g in self.gates:
                g.draw(self.surf)
            t = pygame.time.get_ticks() / 1000
            for p in self.portals:
                p.draw(self.surf, t)
            self.draw_exit()
            self.player.draw(self.surf)
            self.hunter.draw(self.surf)
            self.draw_hud()
            self.draw_maze_timer_bar()

            if self.state == "title":
                self.draw_overlay("VOID MAZE", [
                    "WASD / ARROWS : MOVE",
                    "HOLD E : OPEN NEARBY GATE",
                    "AVOID THE RED HUNTER",
                    "REACH THE EXIT",
                ], CYAN)
            elif self.state == "dead":
                self.draw_overlay("YOU DIED", [
                    f"SCORE : {self.score}",
                    f"LEVEL : {self.level}",
                    "",
                    "PRESS ANY KEY",
                ], (255, 70, 70))

            pygame.display.flip()


if __name__ == "__main__":
    Game().run()