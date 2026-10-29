// =====================================================================
//  Настольный корпус для ESP32-2432S028 (CYD 2.8") — питание от USB
//  OpenSCAD 2021+.  part = "front" | "back" | "stand" | "test" | "assembly"
//
//  Тонкий корпус (как мини-монитор) + отдельная подставка с наклоном.
//  Плата зажата между лицевой панелью и задней крышкой 4 винтами M3x12,
//  которые идут сзади через стойки и отверстия платы в бобышки панели.
//  Корпус просто вставляется в подставку (посадка с зазором) и снимается.
//
//  Размеры платы — типовые для ESP32-2432S028; перед печатью сверьте их
//  тестовой рамкой (part = "test").
// =====================================================================

part = "assembly";

// ---------------- ПЛАТА ESP32-2432S028 ---------------------------------
pcb_l      = 86.0;
pcb_w      = 50.0;
pcb_t      = 1.6;
hole_dx    = 3.5;    // центр крепёжного отверстия от короткого края
hole_dy    = 3.5;    // ... от длинного края
disp_t     = 4.4;    // от лицевой стороны платы до верха стекла (замерить!)
back_clear = 7.0;    // место под деталями на обратной стороне платы
view_l     = 59.0;   // видимая область экрана
view_w     = 44.5;
view_off_x = 0.0;
view_off_y = 0.0;
// micro-USB — на коротком торце x=0, на обратной стороне платы
cyd_usb    = true;
cyd_usb_y  = 14.0;   // центр разъёма от нижнего длинного края (замерить!)
cyd_usb_w  = 12.0;
cyd_usb_h  = 7.0;

// ---------------- КОРПУС ----------------------------------------------
wall     = 2.4;
floor_t  = 2.0;
front_t  = 2.0;
clr      = 0.5;
lip      = 2.0;      // высота замка-ступеньки в стенке
fit      = 0.15;
corner_r = 5.0;
chamfer  = 1.0;      // фаска по лицевому краю
screw_len = 12;      // M3x12
pilot_d  = 2.5;
screw_d  = 3.4;
head_d   = 6.0;
boss_d   = 6.0;
tube_d   = 7.4;
vents    = true;     // вентиляционные прорези на задней крышке

// ---------------- ПОДСТАВКА -------------------------------------------
tilt      = 18;      // наклон экрана назад, градусы от вертикали
stand_w   = 70;      // ширина подставки (короче корпуса — торцы с USB свободны)
seat      = 4;       // толщина подставки под корпусом
grip_back = 26;      // насколько подставка поднимается по задней стенке
grip_front= 3;       // высота переднего бортика (не закрывает экран)
lip_t     = 3;       // толщина бортиков
foot_back = 34;      // вылет основания назад от низа корпуса — устойчивость
foot_front= 6;       // вылет основания вперёд
stand_fit = 0.35;    // зазор посадки корпуса в подставке
feet_d    = 8;       // углубления под резиновые ножки (0 = без них)

$fn = 48;

// ---------------- Производные ------------------------------------------
IL = pcb_l + 2 * clr;
IW = pcb_w + 2 * clr;
OL = IL + 2 * wall;
OW = IW + 2 * wall;
zp = floor_t + back_clear;              // низ платы = линия разъёма
H  = zp + pcb_t + disp_t + front_t;     // толщина корпуса
pilot_end = H - front_t + 1.0;
cb_top    = max(floor_t + 0.5, pilot_end - 0.6 - screw_len);

holes = [[clr + hole_dx, clr + hole_dy], [clr + pcb_l - hole_dx, clr + hole_dy],
         [clr + hole_dx, clr + pcb_w - hole_dy], [clr + pcb_l - hole_dx, clr + pcb_w - hole_dy]];

echo(str("Корпус: ", OL, " x ", OW, " x ", H, " мм; винты 4 x M3x", screw_len,
         ", ввинчивание ≈ ", pilot_end - 0.6 - (zp + pcb_t), " мм"));

// ---------------- Вспомогательное -------------------------------------
module rbox(l, w, h, r) {
  translate([r, r, 0]) linear_extrude(h) offset(r) square([l - 2 * r, w - 2 * r]);
}
module shell_outline(inset, h) {
  translate([-wall + inset, -wall + inset, 0])
    rbox(OL - 2 * inset, OW - 2 * inset, h, max(0.5, corner_r - inset));
}
module usb_cut() {
  if (cyd_usb)
    translate([-wall - 1, clr + cyd_usb_y - cyd_usb_w / 2, zp - 1.4 - cyd_usb_h / 2])
      cube([wall + 2, cyd_usb_w, cyd_usb_h]);
}

// ---------------- Лицевая панель --------------------------------------
module front_shell() {
  z0 = zp;
  difference() {
    union() {
      translate([0, 0, z0 + lip]) difference() {
        shell_outline(0, H - z0 - lip - front_t + 0.01);
        translate([0, 0, -1]) shell_outline(wall / 2, H);
      }
      translate([0, 0, z0 - lip + fit]) difference() {
        shell_outline(wall / 2 + fit, H - z0 + lip - fit);
        translate([0, 0, -1]) shell_outline(wall, H);
      }
      // лицевая пластина с фаской по краю
      translate([0, 0, H - front_t]) hull() {
        shell_outline(0, front_t - chamfer);
        shell_outline(chamfer, front_t);
      }
      for (p = holes) translate([p[0], p[1], z0 + pcb_t]) cylinder(d = boss_d, h = H - front_t - z0 - pcb_t + 0.01);
    }
    // окно экрана с фаской снаружи
    cx = clr + pcb_l / 2 + view_off_x;
    cy = clr + pcb_w / 2 + view_off_y;
    translate([cx, cy, H - front_t - 0.01]) hull() {
      translate([-view_l / 2, -view_w / 2, 0]) cube([view_l, view_w, 0.01]);
      translate([-view_l / 2 - 1.2, -view_w / 2 - 1.2, front_t + 0.01]) cube([view_l + 2.4, view_w + 2.4, 0.01]);
    }
    translate([cx - view_l / 2, cy - view_w / 2, H - front_t - 1]) cube([view_l, view_w, 1.02]);
    for (p = holes) translate([p[0], p[1], z0 - 1]) cylinder(d = pilot_d, h = pilot_end - z0 + 1);
    usb_cut();
  }
}

// ---------------- Задняя крышка ---------------------------------------
module back_shell() {
  difference() {
    union() {
      difference() {
        shell_outline(0, zp + lip - fit);
        translate([0, 0, floor_t]) shell_outline(wall / 2, H);
      }
      difference() {
        shell_outline(0, zp - lip);
        translate([0, 0, floor_t]) shell_outline(wall, H);
      }
      for (p = holes) hull() {
        translate([p[0], p[1], 0]) cylinder(d = tube_d, h = zp);
        translate([p[0] < IL / 2 ? -0.01 : IL - 1, p[1] < IW / 2 ? -0.01 : IW - 1, 0]) cube([1.01, 1.01, zp]);
      }
    }
    for (p = holes) translate([p[0], p[1], 0]) {
      translate([0, 0, -1]) cylinder(d = screw_d, h = zp + 2);
      translate([0, 0, -1]) cylinder(d = head_d, h = cb_top + 1);
    }
    // вентиляция: прорези в верхней части (горячий воздух уходит вверх)
    if (vents) for (i = [0 : 6])
      translate([IL / 2 - 27 + i * 9, IW - 16, -1]) hull() {
        cylinder(d = 2.4, h = floor_t + 2);
        translate([0, 10, 0]) cylinder(d = 2.4, h = floor_t + 2);
      }
    usb_cut();
  }
}

module test_frame() {
  intersection() {
    front_shell();
    translate([-50, -50, H - front_t - disp_t - 0.01]) cube([300, 300, front_t + disp_t + 1]);
  }
}

// ---------------- Подставка -------------------------------------------
// Перевод из координат корпуса (x вдоль, y вверх по экрану, z к зрителю)
// в мировые: экран смотрит в -Y и откинут назад на tilt.
module case_pose() {
  // после наклона нижне-задняя кромка корпуса опускается на H*sin(tilt) — поднимаем
  translate([0, 0, seat + H * sin(tilt)]) rotate([-tilt, 0, 0]) rotate([90, 0, 0])
    translate([-(OL / 2 - wall), wall, -H]) children();
}

// Корпус целиком с зазором — "форма", которую вырезаем из подставки
module case_volume(extra = 0) {
  case_pose() translate([0, 0, -extra]) shell_outline(-extra, H + 2 * extra);
}

module stand() {
  e = stand_fit;
  // контуры в координатах корпуса (y=-wall — низ корпуса, z=0 — задняя стенка, z=H — экран)
  x0 = -wall + (OL - stand_w) / 2;
  module back_plate()   translate([x0, -wall - e - seat, -e - lip_t]) cube([stand_w, grip_back + seat, lip_t]);
  module bottom_plate() translate([x0, -wall - e - seat, -e - lip_t]) cube([stand_w, seat, H + 2 * e + 2 * lip_t]);
  module front_lip()    translate([x0, -wall - e - seat, H + e]) cube([stand_w, grip_front + seat, lip_t]);
  // основание: скруглённая плита
  yf = -(e + lip_t) - foot_front;
  yb = foot_back;
  module foot() hull() for (x = [-stand_w / 2 + 6, stand_w / 2 - 6], y = [yf + 6, yb - 6])
    translate([x, y, 0]) cylinder(r = 6, h = 3);
  module pose() case_pose() children();

  difference() {
    union() {
      hull() { foot(); pose() back_plate(); }
      hull() { foot(); pose() bottom_plate(); }
      hull() { foot(); pose() front_lip(); }
    }
    case_volume(e);
    // вырез сверху, чтобы корпус вставлялся сверху вниз по наклону
    case_pose() translate([-wall - e - 1, -wall + 2, -e]) cube([OL + 2 * e + 2, 200, H + 2 * e]);
    // ровный низ
    translate([-200, -200, -50]) cube([400, 400, 50]);
    // углубления под резиновые ножки
    if (feet_d > 0) for (x = [-stand_w / 2 + 9, stand_w / 2 - 9], y = [yf + 9, yb - 9])
      translate([x, y, -1]) cylinder(d = feet_d + 0.4, h = 2);
  }
}

// ---------------- Макет для сборки -------------------------------------
module dummy_board() {
  color("royalblue") translate([clr, clr, zp]) cube([pcb_l, pcb_w, pcb_t]);
  color("black") translate([clr + (pcb_l - 69.2) / 2, clr, zp + pcb_t]) cube([69.2, pcb_w, disp_t - 0.1]);
  color("deepskyblue") translate([clr + pcb_l / 2 - view_l / 2, clr + pcb_w / 2 - view_w / 2, zp + pcb_t + disp_t - 0.1])
    cube([view_l, view_w, 0.05]);
}

if (part == "front") translate([0, 0, H]) rotate([180, 0, 0]) front_shell();
else if (part == "back") back_shell();
else if (part == "stand") stand();
else if (part == "test") translate([0, 0, H]) rotate([180, 0, 0]) test_frame();
else {
  color("dimgray") stand();
  case_pose() { color("white") back_shell(); color("white") front_shell(); dummy_board(); }
}
