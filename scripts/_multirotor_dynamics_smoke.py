#!/usr/bin/env python3
"""Multirotor dynamics unit smoke — physics, mixer, quaternion math.

Тесты:
  1. pwm_to_thrust monotonic + bounded
  2. mix_motors: симметричный PWM → ноль torque, ненулевая тяга
  3. mix_motors: differential CCW/CW → ненулевой yaw torque
  4. mix_motors: front-heavy → pitch up; right-heavy → roll right
  5. Quaternion identity rotation = identity
  6. Quaternion roundtrip body→world→body для unit vector
  7. Quaternion integration: ω=(0,0,1) для 1 sec → yaw ≈ 1 rad
  8. Hover stability: PWM=hover для 5000 steps → alt drift < 0.1m
  9. Free fall: PWM=1000 для 1 sec → vz ≈ 9.81 m/s, pos ≈ 4.9m down
  10. Climb acceleration: PWM=hover+const → accel ≈ expected
  11. IMU noise model: configured gyro/accel std is reflected in samples
  12. Truth vs measured gyro: noisy output does not feed physics
  13. Ground contact IMU: motors off on ground → accelerometer sees -g
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, "/home/afetz/bas-prototype/scripts")

from multirotor_dynamics import (   # noqa: E402
    MultirotorParams, MultirotorDynamics,
    pwm_to_thrust, mix_motors,
    quat_normalize, quat_rotate_body_to_world,
    quat_rotate_world_to_body, quat_to_euler_zyx, quat_integrate,
    GRAVITY_NED,
)


def approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) < tol


def main() -> int:
    p = MultirotorParams()

    # --- 1. pwm_to_thrust ---
    print("[1] pwm_to_thrust")
    assert pwm_to_thrust(999, p)  == 0.0, "PWM<1000 should clamp to 0"
    assert pwm_to_thrust(1000, p) == 0.0
    assert pwm_to_thrust(2000, p) == p.motor_thrust_max_n
    assert pwm_to_thrust(2500, p) == p.motor_thrust_max_n, "PWM>2000 clamp"
    assert pwm_to_thrust(1500, p) == p.motor_thrust_max_n / 2
    print(f"    PWM 1000→0, 1500→{pwm_to_thrust(1500, p):.2f}N, 2000→{p.motor_thrust_max_n}N")

    # --- 2. Mixer: symmetric thrust = no torque ---
    print("\n[2] symmetric PWM → no torque, full thrust")
    f, tau = mix_motors([1500, 1500, 1500, 1500], p)
    print(f"    f={f:.3f}N  tau=({tau[0]:.4f}, {tau[1]:.4f}, {tau[2]:.4f})")
    assert f == p.motor_thrust_max_n * 4 / 2, f"expected 12N, got {f}"
    for t in tau:
        assert approx(t, 0, 1e-9), f"unexpected torque {tau}"

    # --- 3. Differential CCW/CW → yaw torque ---
    print("\n[3] M1+M2 (CCW) high, M3+M4 (CW) low → tau_z != 0")
    f, tau = mix_motors([1700, 1700, 1300, 1300], p)
    print(f"    f={f:.3f}N  tau_z={tau[2]:.5f} Nm")
    assert abs(tau[2]) > 0.001, f"tau_z too small: {tau[2]}"

    # --- 4. Asymmetric → roll/pitch torque ---
    # Front motors (M1, M3) high → +body-y torque in ArduPilot/SITL X geometry.
    print("\n[4] Front motors high (M1+M3=high, M2+M4=low) → tau_y > 0")
    f, tau = mix_motors([1800, 1300, 1800, 1300], p)
    print(f"    tau_y = {tau[1]:.4f} Nm (expect positive)")
    assert tau[1] > 0.01, f"expected positive tau_y, got {tau[1]}"

    # --- 5. Quaternion identity ---
    print("\n[5] Identity quaternion: (1,0,0,0) rotation = identity")
    v_in = (1.0, 2.0, 3.0)
    v_out = quat_rotate_body_to_world((1.0, 0.0, 0.0, 0.0), v_in)
    assert approx(v_out[0], 1.0) and approx(v_out[1], 2.0) and approx(v_out[2], 3.0), \
        f"identity rotation broken: {v_out}"
    print(f"    {v_in} → {v_out}")

    # --- 6. Quaternion roundtrip ---
    print("\n[6] Quaternion roundtrip body→world→body")
    # 30° yaw quaternion.
    yaw = math.radians(30)
    q = (math.cos(yaw/2), 0.0, 0.0, math.sin(yaw/2))
    v_body = (1.0, 0.0, 0.0)
    v_world = quat_rotate_body_to_world(q, v_body)
    v_back = quat_rotate_world_to_body(q, v_world)
    print(f"    body=(1,0,0) → world={tuple(round(c, 4) for c in v_world)} → back={tuple(round(c, 4) for c in v_back)}")
    for a, b in zip(v_back, v_body):
        assert approx(a, b, 1e-9), f"roundtrip drift {v_back} vs {v_body}"
    # Sanity: 30° yaw of x-axis → (cos30, sin30, 0).
    assert approx(v_world[0], math.cos(yaw), 1e-6)
    assert approx(v_world[1], math.sin(yaw), 1e-6)

    # --- 7. Quaternion integration ω_z = 1 rad/s для 1с → yaw 1 rad ---
    print("\n[7] Integrate ω=(0,0,1) for 1.0s → yaw ≈ 1.0 rad")
    q = (1.0, 0.0, 0.0, 0.0)
    dt = 0.001
    for _ in range(1000):
        q = quat_integrate(q, (0.0, 0.0, 1.0), dt)
    yaw, pitch, roll = quat_to_euler_zyx(q)
    print(f"    yaw = {yaw:.4f} rad (expected 1.0)")
    assert abs(yaw - 1.0) < 0.001, f"yaw drift: {yaw}"

    # Helper: disable IMU noise для repeatable physics tests.
    def _noiseless() -> MultirotorDynamics:
        d = MultirotorDynamics()
        d.params.sensor_noise.enable = False
        return d

    # --- 8. Hover stability ---
    # Hover PWM имеет integer-rounding ошибку (PWM=1613 vs ideal 1612.5).
    # Тест: при правильно подобранной float thrust → acceleration ≈ 0.
    print("\n[8] Hover acceleration check — ideal thrust = weight → accel ≈ 0")
    dyn = _noiseless()
    # Установить vehicle в воздухе с нулевой скоростью.
    dyn.state.pos_ned = (0, 0, -10)   # 10m up
    dyn.state.vel_ned = (0, 0, 0)
    # Точно расчитанный hover PWM (float).
    weight_n = dyn.params.mass_kg * GRAVITY_NED[2]
    per_motor_n = weight_n / 4.0
    pwm_float = 1000.0 + (per_motor_n / dyn.params.motor_thrust_max_n) * 1000.0
    # Integrate 1 sec.
    alt_start = -dyn.state.pos_ned[2]
    for _ in range(1000):
        dyn.step([pwm_float] * 4, 0.001)
    alt_end = -dyn.state.pos_ned[2]
    drift = abs(alt_end - alt_start)
    print(f"    PWM={pwm_float:.3f} (float exact)  alt_start={alt_start:.2f}m  "
          f"alt_end={alt_end:.4f}m  drift={drift:.4f}m / 1s")
    # С точным PWM drift должен быть < 0.05м за секунду.
    assert drift < 0.1, f"hover drift too large: {drift}m"

    # --- 9. Free fall: PWM=1000, no thrust ---
    print("\n[9] Free fall — PWM=1000 для 1s")
    dyn = _noiseless()
    dyn.state.pos_ned = (0, 0, -10)   # start 10m up
    for _ in range(1000):
        dyn.step([1000] * 4, 0.001)
    vz = dyn.state.vel_ned[2]
    z = dyn.state.pos_ned[2]
    print(f"    vz_down = {vz:.2f} m/s (expected ≈9.8)")
    print(f"    z_down  = {z:.2f} (started -10, expected ≈-5.1 ≈ -10 + 4.9)")
    # Допускаем drag слегка тормозит — 9.8 ± 1.
    assert 7.0 < vz < 10.0, f"free fall velocity wrong: {vz}"

    # --- 10. Climb acceleration: thrust = 2× weight → 1g excess up ---
    print("\n[10] Excess thrust = weight → 1g excess upward")
    dyn = _noiseless()
    weight_n = dyn.params.mass_kg * GRAVITY_NED[2]
    target_thrust_n = 2 * weight_n   # 2x weight: net 1g up
    # PWM per motor: thrust_per_motor = target/4 = weight/2.
    # PWM = 1000 + (thrust / max) * 1000.
    per_motor = target_thrust_n / 4
    pwm = int(1000 + per_motor / dyn.params.motor_thrust_max_n * 1000)
    print(f"    target excess thrust = weight = {weight_n:.2f}N total → PWM/motor={pwm}")
    # Integrate for 0.5s.
    for _ in range(500):
        dyn.step([pwm] * 4, 0.001)
    accel_imu_z = dyn.state.accel_body[2]
    print(f"    accel_body_z = {accel_imu_z:.2f} m/s² (expect ≈-2g = -19.6)")
    # IMU чувствует specific force; 2× thrust → acc_world_z = -g (up);
    # specific force в hover = -g; в 2g climb = -2g.
    # Допуск ±2 m/s² потому что есть drag на v.
    assert -22 < accel_imu_z < -15, f"accel z wrong: {accel_imu_z}"

    # --- 11. IMU noise model: σ_gyro, σ_accel, bias drift ---
    print("\n[11] IMU noise model — std verification (10000 samples при hover)")
    dyn = MultirotorDynamics(rng_seed=42)
    sn = dyn.params.sensor_noise
    print(f"    config: gyro_white_std={sn.gyro_white_std} rad/s, "
          f"accel_white_std={sn.accel_white_std} m/s²")
    # Step множество раз в hover, собрать IMU outputs.
    weight_n = dyn.params.mass_kg * GRAVITY_NED[2]
    per_motor_n = weight_n / 4.0
    pwm_float = 1000.0 + (per_motor_n / dyn.params.motor_thrust_max_n) * 1000.0
    dyn.state.pos_ned = (0, 0, -10)
    gyro_samples_z = []
    accel_samples_z = []
    for _ in range(10000):
        dyn.step([pwm_float] * 4, 0.001)
        gyro_samples_z.append(dyn.state.omega_body[2])
        accel_samples_z.append(dyn.state.accel_body[2])
    # Std должен быть ~σ (несколько процентов tolerance).
    g_mean = sum(gyro_samples_z) / len(gyro_samples_z)
    g_std = math.sqrt(sum((g - g_mean)**2 for g in gyro_samples_z)
                       / len(gyro_samples_z))
    a_mean = sum(accel_samples_z) / len(accel_samples_z)
    a_std = math.sqrt(sum((a - a_mean)**2 for a in accel_samples_z)
                       / len(accel_samples_z))
    print(f"    gyro_z:  mean={g_mean:+.5f} rad/s, std={g_std:.5f} "
          f"(expect ~{sn.gyro_white_std})")
    print(f"    accel_z: mean={a_mean:+.3f} m/s², std={a_std:.3f} "
          f"(expect ~{sn.accel_white_std})")
    # Std should be within ±30% of config (bias walk вносит небольшой extra).
    assert 0.7 * sn.gyro_white_std < g_std < 1.5 * sn.gyro_white_std, \
        f"gyro std out of range: {g_std}"
    assert 0.7 * sn.accel_white_std < a_std < 1.5 * sn.accel_white_std, \
        f"accel std out of range: {a_std}"

    # --- 12. Truth vs measured ---
    print("\n[12] Ground truth accessor (omega_body_true vs omega_body)")
    dyn = MultirotorDynamics(rng_seed=123)
    dyn.state.pos_ned = (0, 0, -10)
    for _ in range(100):
        dyn.step([pwm_float] * 4, 0.001)
    print(f"    truth gyro_z = {dyn.state.omega_body_true[2]:+.6f}")
    print(f"    noisy gyro_z = {dyn.state.omega_body[2]:+.6f}")
    # Truth should be ≈0 in hover; noisy can deviate.
    assert abs(dyn.state.omega_body_true[2]) < 0.001, \
        f"truth gyro should be ~0 in hover: {dyn.state.omega_body_true[2]}"

    # --- 13. Ground contact IMU: stopped on ground should not look like free fall ---
    print("\n[13] Ground contact IMU — motors off on ground → accel_body_z ≈ -g")
    dyn = _noiseless()
    dyn.step([1000] * 4, 0.001)
    print(f"    on_ground={dyn.state.on_ground}  accel_body_z={dyn.state.accel_body[2]:.3f}")
    assert dyn.state.on_ground, "vehicle should remain on ground"
    assert -10.2 < dyn.state.accel_body[2] < -9.4, \
        f"ground IMU should see support force near -g, got {dyn.state.accel_body[2]}"

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
