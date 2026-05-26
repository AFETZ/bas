#!/usr/bin/env python3
"""6DOF multirotor dynamics для ArduPilot SITL JSON-FDM closed-loop.

Реалистичная X-config квадрокоптер физика. Закрывает требование
"Для real flight нужно implement minimal multirotor dynamics" из
`docs/stage_4_arducopter_airsim_interface.md` ограничений.

## Frame convention (ArduPilot стандарт)

Body frame: x-forward, y-right, z-down (Forward-Right-Down).
World frame: NED (North-East-Down).
Attitude: quaternion (w, x, y, z); euler ZYX intrinsic (yaw-pitch-roll).

## Motor layout (X-config canonical ArduCopter)

  Forward (+x body)
       M3  M1
        \\/
        /\\
       M2  M4
   (M1 front-right CCW, M2 rear-left CCW, M3 front-left CW, M4 rear-right CW)

ArduCopter X-frame motor ordering (`AP_Motors`):
  ch1 = M1 = front-right, CCW   (roll -, pitch +, yaw +)
  ch2 = M2 = rear-left,   CCW   (roll +, pitch -, yaw +)
  ch3 = M3 = front-left,  CW    (roll +, pitch +, yaw -)
  ch4 = M4 = rear-right,  CW    (roll -, pitch -, yaw -)

Thrust always positive along body -z (up).
Yaw torque: CCW motors generate +yaw torque (right-hand rule about body +z down → CCW spin = -yaw in body frame; depends on convention; используем стандартный CCW=+yaw).

## PWM → thrust

Linear interpolation: PWM ∈ [1000, 2000] μs maps to thrust ∈ [0, MAX_THRUST_N].
PWM < 1000 → 0 thrust, PWM > 2000 → MAX_THRUST_N (clamped).

## Integration

Semi-implicit Euler at SITL rate (typically 1200 Hz). 6DOF state:
  position (3), velocity (3), quaternion (4), body angular rate (3) = 13D.

При sub-millisecond dt оснаружения roll-off — semi-implicit Euler точен
до ~1% за 10 секунд hover; для full mission достаточно.

## IMU output

Specific force (accel что чувствует акселерометр) = (F_body - m·g_body) / m.
В hover acc_body = (0, 0, -g) = (0, 0, -9.81) — гравитация компенсируется
тягой; sensor "видит" -g по z (NED-down). При свободном падении acc_body
= (0, 0, 0).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


GRAVITY_NED = (0.0, 0.0, 9.81)   # NED, +z = down


@dataclass
class SensorNoiseParams:
    """IMU sensor noise model for realistic ArduPilot SITL sensor data.

    EKF convergence прежде всего требует физически корректный gravity/support
    vector (на земле accelerometer ≈ -g, не free-fall). Noise/bias drift здесь
    не маскируют physics bugs, а делают sensor stream ближе к Pixhawk-class IMU:

      INS_GYR_NOISE  ≈ 0.015 rad/s (EK3_GYRO_P_NSE default)
      INS_ACC_NOISE  ≈ 0.35 m/s²   (EK3_ACC_P_NSE default)

    Используем conservative значения, чтобы не вносить лишнюю instability.
    """
    gyro_white_std: float = 0.005          # rad/s — белый шум на каждый sample
    accel_white_std: float = 0.05          # m/s²  — белый шум
    gyro_bias_walk_std: float = 0.0001     # rad/s/√s — random walk bias drift
    accel_bias_walk_std: float = 0.001     # m/s²/√s
    enable: bool = True                    # disable для unit tests


@dataclass
class MultirotorParams:
    """Параметры физической модели Iris-like quadrotor (~1.5 кг)."""
    mass_kg: float = 1.5
    arm_length_m: float = 0.255          # motor distance от центра
    motor_thrust_max_n: float = 6.0      # @ PWM=2000 (1.6×weight headroom)
    motor_thrust_min_pwm: int = 1000     # zero thrust below
    motor_thrust_max_pwm: int = 2000     # max thrust at this PWM
    motor_torque_factor: float = 0.016   # ψ-torque per N of thrust (Nm/N)
    # Inertia diag (kg·m²). Для Iris-class quad ≈ 0.011/0.011/0.021.
    inertia_xx: float = 0.011
    inertia_yy: float = 0.011
    inertia_zz: float = 0.021
    # Linear drag coefficient (N·s/m per axis) — simple Stokes drag.
    drag_linear: float = 0.10
    # Angular drag coefficient (Nm·s/rad).
    drag_angular: float = 0.001
    # IMU noise/bias drift для realistic SITL sensor output.
    sensor_noise: SensorNoiseParams = field(default_factory=SensorNoiseParams)


def quat_normalize(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    w, x, y, z = q
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if n < 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    return (w / n, x / n, y / n, z / n)


def quat_rotate_body_to_world(
    q: tuple[float, float, float, float],
    v_body: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Rotate vector из body frame в world frame через quaternion."""
    w, x, y, z = q
    vx, vy, vz = v_body
    # v_world = q ⊗ v_body ⊗ q⁻¹ (где v как pure quaternion).
    # Reduced 9-mul formula:
    t2 = 2 * (y * vz - z * vy)
    t3 = 2 * (z * vx - x * vz)
    t4 = 2 * (x * vy - y * vx)
    wx_world = vx + w * t2 + y * t4 - z * t3
    wy_world = vy + w * t3 + z * t2 - x * t4
    wz_world = vz + w * t4 + x * t3 - y * t2
    return (wx_world, wy_world, wz_world)


def quat_rotate_world_to_body(
    q: tuple[float, float, float, float],
    v_world: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Inverse rotation: world → body. Используем conjugate quaternion."""
    w, x, y, z = q
    return quat_rotate_body_to_world((w, -x, -y, -z), v_world)


def quat_to_euler_zyx(q: tuple[float, float, float, float]) -> tuple[float, float, float]:
    """Quaternion → euler ZYX (yaw, pitch, roll) в радианах."""
    w, x, y, z = q
    # roll (x-axis rotation)
    sinr = 2 * (w * x + y * z)
    cosr = 1 - 2 * (x * x + y * y)
    roll = math.atan2(sinr, cosr)
    # pitch (y-axis rotation)
    sinp = 2 * (w * y - z * x)
    pitch = math.asin(max(-1.0, min(1.0, sinp)))
    # yaw (z-axis rotation)
    siny = 2 * (w * z + x * y)
    cosy = 1 - 2 * (y * y + z * z)
    yaw = math.atan2(siny, cosy)
    return (yaw, pitch, roll)


def quat_integrate(
    q: tuple[float, float, float, float],
    omega_body: tuple[float, float, float],
    dt: float,
) -> tuple[float, float, float, float]:
    """Integrate quaternion через body angular rate. q̇ = 0.5 · q ⊗ ω."""
    w, x, y, z = q
    wx, wy, wz = omega_body
    dw = 0.5 * (-x * wx - y * wy - z * wz)
    dx = 0.5 * ( w * wx + y * wz - z * wy)
    dy = 0.5 * ( w * wy - x * wz + z * wx)
    dz = 0.5 * ( w * wz + x * wy - y * wx)
    return quat_normalize((w + dw * dt, x + dx * dt, y + dy * dt, z + dz * dt))


# ---------------------------------------------------------------------------
# Motor mixer
# ---------------------------------------------------------------------------
def pwm_to_thrust(pwm: float, params: MultirotorParams) -> float:
    """Linear PWM → thrust mapping, clamped to [0, max]."""
    pwm = max(params.motor_thrust_min_pwm,
              min(params.motor_thrust_max_pwm, pwm))
    norm = (pwm - params.motor_thrust_min_pwm) / (
        params.motor_thrust_max_pwm - params.motor_thrust_min_pwm)
    return norm * params.motor_thrust_max_n


def mix_motors(
    pwm: list[float], params: MultirotorParams,
) -> tuple[float, tuple[float, float, float]]:
    """Из 4-канального PWM → суммарная тяга (N) + torque body (Nm).

    Возвращает (F_thrust_total_n, (tau_x, tau_y, tau_z)).
    """
    if len(pwm) < 4:
        return (0.0, (0.0, 0.0, 0.0))
    # Motor thrusts.
    t1 = pwm_to_thrust(pwm[0], params)   # FR CCW
    t2 = pwm_to_thrust(pwm[1], params)   # RL CCW
    t3 = pwm_to_thrust(pwm[2], params)   # FL CW
    t4 = pwm_to_thrust(pwm[3], params)   # RR CW

    # Total thrust (always +body up = -body z).
    f_total = t1 + t2 + t3 + t4

    # Arm at 45° → effective torque-arm = arm_length / sqrt(2).
    eff = params.arm_length_m / math.sqrt(2.0)

    # Roll torque (body x = forward): right-side motors (M1,M4) push up
    # → right wing goes up → roll +x = right wing down means: right ↓
    # When motor under right wing pushes UP (+thrust on body-z up means -z body),
    # right wing receives upward force → roll torque about body +x is:
    #    tau_x = (M3+M2 left)·eff - (M1+M4 right)·eff
    # Convention: positive tau_x rotates right wing down (roll right).
    # Per ArduCopter mixer matrix:
    #   roll =  -ch1 + ch2 - ch3 + ch4  (но это для commanded; реверс для физики)
    # Здесь физика: правые motors (M1, M4) поднимают правую сторону → roll влево (-x)
    # Левая сторона (M2, M3) поднимает левую → roll вправо (+x).
    tau_x = ((t2 + t3) - (t1 + t4)) * eff

    # Pitch torque matches ArduPilot/SITL X-frame geometry:
    # r × F for front motors (M1, M3) gives +body-y torque; rear motors
    # (M2, M4) give -body-y torque.
    tau_y = ((t1 + t3) - (t2 + t4)) * eff

    # Yaw torque (body z = down): CCW motors дают reaction torque вокруг
    # их оси rotation. Если ротор crутится CCW (вид сверху), реакция на
    # body = CW relative to body +z (z вниз). Convention: tau_z > 0 = yaw
    # right (clockwise top-down view).
    # M1 + M2 are CCW (reaction CW = +tau_z when z is down? зависит от знака
    # rotation axis vs body z). Standard ArduCopter: CCW motors create +yaw torque.
    # Используем: tau_z = (M1 + M2)·k_q - (M3 + M4)·k_q
    tau_z = (t1 + t2 - t3 - t4) * params.motor_torque_factor

    return (f_total, (tau_x, tau_y, tau_z))


# ---------------------------------------------------------------------------
# Dynamics state
# ---------------------------------------------------------------------------
@dataclass
class DynamicsState:
    """13D state vector + IMU readings (с noise если включён).

    Поля `accel_body` и `omega_body` published из `step()` — содержат
    measured values (с noise + bias drift) которые попадают в EKF.
    Чистая physics state (без noise) — в `accel_body_true` и `omega_body_true`.
    """
    pos_ned: tuple[float, float, float] = (0.0, 0.0, 0.0)
    vel_ned: tuple[float, float, float] = (0.0, 0.0, 0.0)
    quat: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    omega_body: tuple[float, float, float] = (0.0, 0.0, 0.0)
    accel_body: tuple[float, float, float] = (0.0, 0.0, -9.81)
    on_ground: bool = True
    # Ground truth (без noise) — для logging/comparison.
    omega_body_true: tuple[float, float, float] = (0.0, 0.0, 0.0)
    accel_body_true: tuple[float, float, float] = (0.0, 0.0, -9.81)
    # Bias drift state (random walk per axis).
    gyro_bias: tuple[float, float, float] = (0.0, 0.0, 0.0)
    accel_bias: tuple[float, float, float] = (0.0, 0.0, 0.0)


class MultirotorDynamics:
    """Quadrotor 6DOF physics integrator для ArduPilot SITL.

    state.pos_ned, vel_ned — мир NED.
    state.quat — body→world rotation.
    state.omega_body — angular rate в body frame **с noise** (IMU output).
    state.omega_body_true — true angular rate (без noise).
    """

    def __init__(self, params: MultirotorParams | None = None,
                 ground_z_down: float = 0.0,
                 rng_seed: int | None = None) -> None:
        self.params = params or MultirotorParams()
        self.state = DynamicsState()
        self.ground_z_down = ground_z_down
        self._t = 0.0
        self._weight_n = self.params.mass_kg * GRAVITY_NED[2]
        self._rng = random.Random(rng_seed)

    def reset(self) -> None:
        self.state = DynamicsState()
        self._t = 0.0

    def _apply_imu_noise(
        self, gyro_true: tuple[float, float, float],
        accel_true: tuple[float, float, float], dt: float,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """Apply Gaussian white noise + random walk bias к IMU outputs.

        Возвращает (noisy_gyro, noisy_accel) — что должно попасть в EKF.
        Updates self.state.gyro_bias / accel_bias в place.
        """
        sn = self.params.sensor_noise
        if not sn.enable:
            return gyro_true, accel_true
        # Bias random walk: Δbias = σ_walk · √dt · N(0,1).
        sqrt_dt = math.sqrt(max(dt, 1e-9))
        gb = self.state.gyro_bias
        ab = self.state.accel_bias
        new_gb = tuple(gb[i] + sn.gyro_bias_walk_std * sqrt_dt * self._rng.gauss(0, 1)
                       for i in range(3))
        new_ab = tuple(ab[i] + sn.accel_bias_walk_std * sqrt_dt * self._rng.gauss(0, 1)
                       for i in range(3))
        self.state.gyro_bias = new_gb
        self.state.accel_bias = new_ab
        # Output = true + bias + white_noise.
        noisy_gyro = tuple(gyro_true[i] + new_gb[i]
                          + sn.gyro_white_std * self._rng.gauss(0, 1)
                          for i in range(3))
        noisy_accel = tuple(accel_true[i] + new_ab[i]
                           + sn.accel_white_std * self._rng.gauss(0, 1)
                           for i in range(3))
        return noisy_gyro, noisy_accel

    def step(self, pwm: list[float], dt: float) -> None:
        """One physics tick — integrate state forward на dt seconds."""
        p = self.params
        f_thrust, torque_body = mix_motors(pwm, p)

        # Force в body frame: thrust along body -z.
        f_body = (0.0, 0.0, -f_thrust)
        # Drag (linear) в body frame: -k · v_body.
        v_body = quat_rotate_world_to_body(self.state.quat, self.state.vel_ned)
        f_body_drag = tuple(-p.drag_linear * v for v in v_body)
        f_body_total = tuple(f_body[i] + f_body_drag[i] for i in range(3))

        # Force в world frame = R · f_body + gravity.
        f_world = quat_rotate_body_to_world(self.state.quat, f_body_total)
        f_world_total = (
            f_world[0] + p.mass_kg * GRAVITY_NED[0],
            f_world[1] + p.mass_kg * GRAVITY_NED[1],
            f_world[2] + p.mass_kg * GRAVITY_NED[2],
        )
        accel_world = tuple(f / p.mass_kg for f in f_world_total)

        # Integrate velocity (semi-implicit).
        new_vel = tuple(self.state.vel_ned[i] + accel_world[i] * dt
                        for i in range(3))
        # Integrate position using new velocity.
        new_pos = tuple(self.state.pos_ned[i] + new_vel[i] * dt
                        for i in range(3))

        # Ground contact: если падаем вниз через ground_z_down, остановиться.
        on_ground = False
        if new_pos[2] >= self.ground_z_down:
            new_pos = (new_pos[0], new_pos[1], self.ground_z_down)
            new_vel = (new_vel[0] * 0.5, new_vel[1] * 0.5, min(0.0, new_vel[2]))
            on_ground = True

        # Specific force (что чувствует акселерометр) = R⁻¹ · (a_world - g).
        # Важно: на земле ground reaction отменяет downward acceleration.
        # Без этого стоящий аппарат выглядит как free-fall (accel≈0), и
        # ArduPilot AHRS/DCM не может стабильно выровнять roll/pitch.
        accel_world_for_imu = accel_world
        if on_ground and accel_world[2] > 0.0:
            accel_world_for_imu = (accel_world[0], accel_world[1], 0.0)
        a_minus_g = (
            accel_world_for_imu[0] - GRAVITY_NED[0],
            accel_world_for_imu[1] - GRAVITY_NED[1],
            accel_world_for_imu[2] - GRAVITY_NED[2],
        )
        accel_imu_body_true = quat_rotate_world_to_body(self.state.quat, a_minus_g)
        # True (без noise) — потребляется physics logging / SITL sensor path.
        self.state.accel_body_true = accel_imu_body_true

        # Angular dynamics. ω̇ = I⁻¹ · (τ - ω × (I·ω)) — Euler's equation.
        # ВАЖНО: использовать TRUE omega для физики (не noisy IMU output),
        # иначе noise компаундится через time → divergence.
        wx, wy, wz = self.state.omega_body_true
        Ix, Iy, Iz = p.inertia_xx, p.inertia_yy, p.inertia_zz
        # Gyroscopic precession ω × Iω.
        gyro_torque = (
            wy * Iz * wz - wz * Iy * wy,
            wz * Ix * wx - wx * Iz * wz,
            wx * Iy * wy - wy * Ix * wx,
        )
        net_torque = (
            torque_body[0] - gyro_torque[0] - p.drag_angular * wx,
            torque_body[1] - gyro_torque[1] - p.drag_angular * wy,
            torque_body[2] - gyro_torque[2] - p.drag_angular * wz,
        )
        omega_dot = (
            net_torque[0] / Ix,
            net_torque[1] / Iy,
            net_torque[2] / Iz,
        )
        new_omega = (
            wx + omega_dot[0] * dt,
            wy + omega_dot[1] * dt,
            wz + omega_dot[2] * dt,
        )

        # Integrate quaternion (используя TRUE new_omega).
        new_quat = quat_integrate(self.state.quat, new_omega, dt)

        # Commit physics state (true).
        self.state.pos_ned = new_pos
        self.state.vel_ned = new_vel
        self.state.quat = new_quat
        self.state.omega_body_true = new_omega
        self.state.on_ground = on_ground

        # Apply IMU noise model — published omega_body/accel_body — то что
        # EKF "видит" через датчик. Не feed-back в physics dynamics.
        noisy_gyro, noisy_accel = self._apply_imu_noise(
            new_omega, self.state.accel_body_true, dt,
        )
        self.state.omega_body = noisy_gyro
        self.state.accel_body = noisy_accel

        self._t += dt

    @property
    def t(self) -> float:
        return self._t

    @property
    def hover_pwm_estimate(self) -> int:
        """Какой PWM нужен для hover (thrust = weight)."""
        per_motor_n = self._weight_n / 4.0
        norm = per_motor_n / self.params.motor_thrust_max_n
        return int(self.params.motor_thrust_min_pwm + norm * (
            self.params.motor_thrust_max_pwm - self.params.motor_thrust_min_pwm))


__all__ = [
    "MultirotorParams", "SensorNoiseParams",
    "DynamicsState", "MultirotorDynamics",
    "pwm_to_thrust", "mix_motors",
    "quat_normalize", "quat_rotate_body_to_world",
    "quat_rotate_world_to_body", "quat_to_euler_zyx", "quat_integrate",
    "GRAVITY_NED",
]
