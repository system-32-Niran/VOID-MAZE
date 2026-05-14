"""
Hand-crafted DBD facility map — matches the blueprint image.

Grid: ROWS=35 rows x COLS=51 cols  (same as game.py's ROWS/COLS)
All cells start as walls (True); rooms are carved open (False).

Room layout (top-left origin):
  Upper section (rows 1-20):
    Waiting Room         rows  2- 9, cols  1- 7
    Gate Control Panel   rows 11-20, cols  1- 6
    Phòng Chờ            rows  2- 9, cols  9-15
    Phạm Vi Freezing (L) rows 11-20, cols  8-15
    Buồng Đóng Băng      rows  2-10, cols 17-30
    Khu Vực NL Tâm       rows 12-20, cols 18-30
    Kho Hàng (center)    rows 12-20, cols 32-37
    Phòng Bảo Vệ         rows  2-10, cols 32-37
    CCTV Room            rows  2-20, cols 39-43
    Khu Vực Bảo Vệ       rows  2-20, cols 45-49

  Main Hallway (row 22):  cols 1-49

  Lower section (rows 24-33):
    Kho Hàng (bottom)    rows 24-33, cols  1-11
    Khu Vực Generator    rows 24-33, cols 12-22
    Phòng Thí Nghiệm     rows 24-33, cols 23-33
    Phòng Ăn             rows 24-33, cols 34-41
    Spawn Area           rows 24-33, cols 42-49
"""

ROWS = 35
COLS = 51


def build_dbd_facility():
    """
    Build the hand-crafted DBD facility map.

    Returns
    -------
    walls          : list[list[bool]]  True = solid wall
    gen_positions  : list[(r, c)]      4 generator locations
    pod_positions  : list[(r, c)]      5 freezing-pod locations
    runner_spawns  : list[(r, c)]      runner starting cells
    hunter_spawn   : (r, c)            hunter starting cell
    exit_pos       : (r, c)            cell runners reach to escape
    """
    walls = [[True] * COLS for _ in range(ROWS)]

    def carve(r1, c1, r2, c2):
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                walls[r][c] = False

    def door(r, c):
        walls[r][c] = False

    # ── UPPER SECTION (rows 1–20) ─────────────────────────────────────────────

    # Exit Gate / Waiting Room  (top-left)
    carve(2, 1, 9, 7)
    # Gate Control Panel  (below Waiting Room)
    carve(11, 1, 20, 6)
    door(10, 3)   # Waiting Room ↔ Gate Control Panel

    door(5, 8)    # Waiting Room → Phòng Chờ

    # Phòng Chờ  (upper-left center)
    carve(2, 9, 9, 15)
    door(10, 12)  # Phòng Chờ ↔ Phạm Vi Freezing (L)
    door(5, 16)   # Phòng Chờ → Buồng Đóng Băng

    # Phạm Vi Freezing – Left zone  (holds 2 pods)
    carve(11, 8, 20, 15)
    door(15, 7)   # Gate Control ↔ Left Freezing  (through col-7 wall)
    door(15, 16)  # Left Freezing ↔ Central Energy  (2-cell corridor)
    door(15, 17)

    # Buồng Đóng Băng – Upper pods area  (holds 3 pods)
    carve(2, 17, 10, 30)
    door(11, 23)  # Upper Pods → Central Energy  (through row-11 wall)

    # Khu Vực Năng Lượng Tâm – Central Energy Zone  (2 generators)
    carve(12, 18, 20, 30)

    # Kho Hàng – center-right storage
    carve(12, 32, 20, 37)
    door(16, 31)  # Central Energy ↔ Kho Hàng

    # Phòng Bảo Vệ – Security Room  (upper-right center)
    carve(2, 32, 10, 37)
    door(6, 31)   # Buồng Đóng Băng ↔ Security Room
    door(11, 34)  # Security Room ↔ Kho Hàng  (vertical connector)

    # CCTV Room Area  (right side)
    carve(2, 39, 20, 43)
    door(6, 38)   # Security Room ↔ CCTV
    door(16, 38)  # Kho Hàng ↔ CCTV

    # Khu Vực Bảo Vệ + Secondary Exit  (far right, 1 generator)
    carve(2, 45, 20, 49)
    door(10, 44)  # CCTV ↔ Khu Vực Bảo Vệ

    # ── MAIN HALLWAY (row 22) ─────────────────────────────────────────────────
    carve(22, 1, 22, 49)

    # Upper rooms → Hallway  (doorways through row-21 wall)
    door(21, 3)   # Gate Control Panel
    door(21, 12)  # Phạm Vi Freezing (L)
    door(21, 23)  # Central Energy Zone
    door(21, 35)  # Kho Hàng center
    door(21, 41)  # CCTV Room
    door(21, 47)  # Khu Vực Bảo Vệ

    # ── LOWER SECTION (rows 24–33) ────────────────────────────────────────────

    # Hallway → Lower rooms  (doorways through row-23 wall)
    door(23, 5)   # → Kho Hàng bottom
    door(23, 16)  # → Khu Vực Generator
    door(23, 27)  # → Phòng Thí Nghiệm
    door(23, 37)  # → Phòng Ăn
    door(23, 45)  # → Spawn Area

    # Kho Hàng  (bottom-left)
    carve(24, 1, 33, 11)
    # Khu Vực Generator  (1 generator)
    carve(24, 12, 33, 22)
    # Phòng Thí Nghiệm  (Lab)
    carve(24, 23, 33, 33)
    # Phòng Ăn  (Dining)
    carve(24, 34, 33, 41)
    # Spawn Area  (runner spawn – bottom-right)
    carve(24, 42, 33, 49)

    # ── FIXED OBJECT POSITIONS ────────────────────────────────────────────────

    # 4 Generators
    gen_positions = [
        (16, 22),   # Khu Vực NL Tâm  – Máy Phát Điện 1
        (16, 26),   # Khu Vực NL Tâm  – Máy Phát Điện 2
        (28, 17),   # Khu Vực Generator – Máy Phát Điện 3
        (14, 47),   # Khu Vực Bảo Vệ  – Máy Phát Điện 4
    ]

    # 5 Freezing Pods — spread across the whole facility
    pod_positions = [
        (5, 23),    # Buồng Đóng Băng    – upper-center
        (6, 40),    # CCTV Room Area     – upper-right
        (16, 24),   # Khu Vực NL Tâm    – mid-center
        (28,  6),   # Kho Hàng (bottom)  – lower-left
        (28, 28),   # Phòng Thí Nghiệm  – lower-center
    ]

    # Runner spawns  (Spawn Area, bottom-right)
    runner_spawns = [(27, 44), (27, 47), (30, 44), (30, 47)]

    # Hunter spawn  (Central Energy Zone – heart of the facility)
    hunter_spawn = (16, 24)

    # Exit position  (inside Waiting Room, near the left exit gate)
    exit_pos = (5, 2)

    return walls, gen_positions, pod_positions, runner_spawns, hunter_spawn, exit_pos
