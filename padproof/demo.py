"""Repeatable simulated AOI image with overlapping pad inspection windows."""

import cv2
import numpy as np

SIZE = (1080, 740)
PADS = [
    ('J1', [266, 177, 535, 343]), ('J2', [463, 177, 739, 343]),
    ('J3', [267, 408, 534, 573]), ('J4', [465, 408, 735, 573]),
    ('TP5', [800, 396, 964, 527]), ('TP6', [801, 543, 963, 660]),
]


def image():
    width, height = SIZE
    rng = np.random.default_rng(177)
    yy, xx = np.mgrid[:height, :width]
    light = np.exp(-((xx - 688) ** 2 + (yy - 340) ** 2) / (2 * 410 ** 2))
    film = 1.5 * rng.standard_normal((height, width)) + 15 * light
    board = np.clip(np.array([12, 51, 48])[None, None] + film[..., None], 0, 255).astype(np.uint8)

    for i in range(12):
        right = i % 2 == 0
        x0 = 89 + 59 * i
        y0 = 74 + i * 16
        bend_x = x0 + (54 if right else -45)
        route = np.array([(x0, 22), (x0, y0), (bend_x, y0 + 45), (bend_x, 648),
                          (bend_x + 30, 684), (bend_x + 30, 721)])
        cv2.polylines(board, [route], False, (23, 76, 67), 9, cv2.LINE_AA)
        cv2.polylines(board, [route], False, (39, 87, 70), 2, cv2.LINE_AA)
    for x, y in [(102, 117), (971, 78), (126, 602), (992, 648), (92, 492), (939, 197)]:
        cv2.circle(board, (x, y), 27, (109, 104, 60), 8, cv2.LINE_AA)
        cv2.circle(board, (x, y), 15, (10, 24, 24), -1, cv2.LINE_AA)
        cv2.circle(board, (x, y), 12, (6, 18, 18), 2, cv2.LINE_AA)
    for x, y, label in [(244, 151, 'J1'), (700, 151, 'J2'), (244, 387, 'J3'),
                        (700, 387, 'J4'), (802, 379, 'TP5'), (802, 691, 'TP6')]:
        cv2.putText(board, label, (x, y), cv2.FONT_HERSHEY_SIMPLEX, .72, (141, 171, 148), 2, cv2.LINE_AA)
    cv2.putText(board, 'PP-SAMPLE / 06', (65, 346), cv2.FONT_HERSHEY_SIMPLEX,
                .55, (123, 144, 126), 1, cv2.LINE_AA)

    for x, y, w, h in [(280, 192, 196, 127), (520, 193, 202, 126),
                       (280, 426, 195, 123), (520, 425, 198, 125),
                       (818, 414, 128, 98), (820, 558, 125, 89)]:
        cv2.rectangle(board, (x, y), (x+w, y+h), (102, 89, 49), -1)
        cv2.rectangle(board, (x, y), (x+w, y+h), (127, 111, 59), 2, cv2.LINE_AA)
    paste = np.zeros((height, width), np.uint8)
    polygons = [
        [(294, 206), (412, 209), (444, 222), (454, 246), (466, 251), (474, 265),
         (503, 268), (515, 235), (526, 232), (533, 250), (526, 281), (537, 291),
         (507, 302), (476, 282), (459, 277), (441, 306), (390, 306), (342, 312), (298, 299)],
        [(560, 207), (656, 203), (706, 219), (711, 263), (681, 307), (568, 308),
         (537, 298), (525, 286), (515, 292), (507, 281), (520, 272), (537, 277), (547, 231)],
        [(297, 441), (393, 438), (443, 443), (454, 475), (445, 518), (409, 530), (306, 527)],
        [(555, 438), (695, 442), (710, 468), (704, 511), (679, 534), (567, 535), (548, 493)],
        [(825, 434), (889, 427), (934, 437), (934, 478), (884, 493), (827, 484)],
        [(825, 569), (911, 566), (932, 597), (922, 628), (842, 629), (827, 612)],
    ]
    cv2.fillPoly(paste, [np.array(vertices) for vertices in polygons], 255, cv2.LINE_AA)
    cv2.ellipse(paste, (499, 481), (15, 9), 26, 0, 360, 255, -1, cv2.LINE_AA)
    cv2.circle(paste, (459, 506), 3, 255, -1, cv2.LINE_AA)
    cv2.circle(paste, (359, 258), 8, 0, -1, cv2.LINE_AA)
    cv2.ellipse(paste, (887, 454), (12, 7), 12, 0, 360, 0, -1, cv2.LINE_AA)
    grains = 0.7 * rng.standard_normal((height, width)) + 0.75 * cv2.GaussianBlur(
        rng.standard_normal((height, width)), (0, 0), 1.5)
    reflection = 166 + 29 * light + 19 * grains + 7 * np.sin(xx / 20 + yy / 28)
    texture = np.clip(reflection[..., None] + np.array([3, 0, -9])[None, None], 0, 255).astype(np.uint8)
    weight = paste[..., None].astype(np.float32) / 255
    board = (board * (1 - weight) + texture * weight).astype(np.uint8)
    cv2.ellipse(board, (634, 236), (13, 8), 14, 0, 360, (255, 249, 238), -1, cv2.LINE_AA)
    cv2.rectangle(board, (823, 118), (931, 120), (190, 175, 134), -1)
    for x in [823, 877, 931]:
        cv2.line(board, (x, 111), (x, 126), (190, 175, 134), 2, cv2.LINE_AA)
    cv2.putText(board, '2.00 mm', (833, 104), cv2.FONT_HERSHEY_SIMPLEX, .56,
                (196, 178, 130), 1, cv2.LINE_AA)
    return board
