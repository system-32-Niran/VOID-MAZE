"""
VOID MAZE — v1.1
Thêm hunter (kẻ săn đuổi) tìm đường đến player bằng BFS.

pip install pygame
python game_v1_1.py
"""

import pygame
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

FPS          = 60
MOVE_DELAY   = 0.11
HUNTER_DELAY = 0.38

BLACK = (0, 0, 0)
CYAN  = (0, 255, 200)
WHITE = (255, 255, 255)


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


def is_wall(walls, r, c):
    if r < 0 or r >= ROWS or c < 0 or c >= COLS:
        return True
    return walls[r][c]


def bfs(walls, sr, sc, tr, tc):
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
            if 0 <= nr < ROWS and 0 <= nc < COLS and not is_wall(walls, nr, nc) and dist[nr][nc] == -1:
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


def cell_xy(r, c):
    return (c * CELL + CELL // 2, r * CELL + CELL // 2)


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
        pygame.display.set_caption("VOID MAZE v1.1")
        self.clock = pygame.time.Clock()
        self.font_xl  = pygame.font.Font(None, 72)
        self.font_med = pygame.font.Font(None, 40)
        self.state = "title"
        self.reset()

    def reset(self):
        self.level = 1
        self.score = 0
        self.new_level()

    def new_level(self):
        self.walls    = build_maze(ROWS, COLS)
        self.player   = Player(1, 1)
        self.hunter   = Hunter(ROWS - 2, COLS - 2)
        self.exit_pos = (ROWS - 2, COLS - 2)
        self.move_timer   = 0.0
        self.hunter_timer = 0.0

    def try_move(self, dr, dc):
        nr = self.player.r + dr
        nc = self.player.c + dc
        if is_wall(self.walls, nr, nc):
            return
        self.player.trail.append((self.player.r, self.player.c))
        self.player.r = nr
        self.player.c = nc
        self.score += 1
        if (self.player.r, self.player.c) == self.exit_pos:
            self.score += 100
            self.level += 1
            self.new_level()
        elif (self.player.r, self.player.c) == (self.hunter.r, self.hunter.c):
            self.state = "dead"

    def update_hunter(self, dt):
        self.hunter_timer += dt
        if self.hunter_timer < HUNTER_DELAY:
            return
        self.hunter_timer = 0
        path = bfs(self.walls, self.hunter.r, self.hunter.c, self.player.r, self.player.c)
        if path:
            self.hunter.trail.append((self.hunter.r, self.hunter.c))
            self.hunter.r, self.hunter.c = path[0]
        if (self.player.r, self.player.c) == (self.hunter.r, self.hunter.c):
            self.state = "dead"

    def draw_maze(self):
        self.surf.fill(BLACK)
        for r in range(ROWS):
            for c in range(COLS):
                if self.walls[r][c]:
                    rect = pygame.Rect(c * CELL, r * CELL, CELL, CELL)
                    pygame.draw.rect(self.surf, (0, 40, 70),  rect)
                    pygame.draw.rect(self.surf, (0, 90, 140), rect, 1)

    def draw_exit(self):
        er, ec = self.exit_pos
        x, y   = cell_xy(er, ec)
        size   = int(CELL * 0.35)
        points = [(x, y - size), (x + size, y), (x, y + size), (x - size, y)]
        pygame.draw.polygon(self.surf, WHITE, points)

    def draw_hud(self):
        lv = self.font_med.render(f"LEVEL {self.level}   SCORE {self.score}", True, CYAN)
        self.surf.blit(lv, (12, 8))

    def draw_overlay(self, title, lines, color):
        ow, oh = 700, 300
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
                self.move_timer += dt
                if self.move_timer >= MOVE_DELAY:
                    for key, (dr, dc) in move_keys.items():
                        if pressed[key]:
                            self.move_timer = 0
                            self.try_move(dr, dc)
                            break
                self.update_hunter(dt)

            self.draw_maze()
            self.draw_exit()
            self.player.draw(self.surf)
            self.hunter.draw(self.surf)
            self.draw_hud()

            if self.state == "title":
                self.draw_overlay("VOID MAZE", [
                    "WASD / ARROWS : MOVE",
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