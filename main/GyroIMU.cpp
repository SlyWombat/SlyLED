/*
 * GyroIMU.cpp — QMI8658 6-axis IMU driver with complementary filter.
 *
 * QMI8658A register map (I2C address 0x6B):
 *   0x00 WHO_AM_I  → 0x05 (constant)
 *   0x02 CTRL1     — SPI/I2C interface config (default 0x40 = I2C + addr auto-inc)
 *   0x03 CTRL2     — accel ODR[3:0] + FSR[5:4]
 *   0x04 CTRL3     — gyro  ODR[3:0] + FSR[6:4]
 *   0x08 CTRL7     — enable: bit0=accel, bit1=gyro
 *   0x35 ACCEL_X_L .. 0x40 GYRO_Z_H  — 12-byte raw output block
 *
 * Complementary filter:
 *   filtered = α × (prev + gyro × dt) + (1 − α) × accel_angle
 *   α = 0.98 keeps fast gyro response while slowly correcting accel drift.
 *   Yaw is gyro-only (no magnetometer); call gyroIMUZero() to reset.
 */

#include "BoardConfig.h"

#ifdef BOARD_GYRO

#include "GyroIMU.h"
#include "GyroBoard.h"
#include <Arduino.h>
#include <Wire.h>
#include <math.h>

// ── QMI8658 register addresses ────────────────────────────────────────────────
static constexpr uint8_t QMI_WHO_AM_I   = 0x00;
static constexpr uint8_t QMI_CTRL1      = 0x02;
static constexpr uint8_t QMI_CTRL2      = 0x03;  // accel config
static constexpr uint8_t QMI_CTRL3      = 0x04;  // gyro config
static constexpr uint8_t QMI_CTRL7      = 0x08;  // enable
static constexpr uint8_t QMI_ACCEL_X_L  = 0x35;  // first raw output register

static constexpr uint8_t QMI_WHO_AM_I_VAL = 0x05;

// ── Sensor configuration ──────────────────────────────────────────────────────
// CTRL2: aFS[5:4]=10 (±8 g, 4096 LSB/g) + aODR[3:0]=0101 (256 Hz)
static constexpr uint8_t QMI_CTRL2_VAL = 0x25;
// CTRL3: gFS[6:4]=101 (±512 dps, 64 LSB/dps) + gODR[3:0]=0101 (256 Hz)
static constexpr uint8_t QMI_CTRL3_VAL = 0x55;

static constexpr float ACCEL_SENS = 4096.0f;  // LSB per g  (±8 g range)
static constexpr float GYRO_SENS  =   64.0f;  // LSB per dps (±512 dps range)
// Use unique names — Arduino.h already #defines DEG_TO_RAD / IMU_RAD2DEG
static constexpr float IMU_DEG2RAD = (float)M_PI / 180.0f;
static constexpr float IMU_RAD2DEG = 180.0f / (float)M_PI;
static constexpr float CF_ALPHA   = 0.98f;    // complementary filter weight

// ── Filter state ─────────────────────────────────────────────────────────────
static float s_roll  = 0.0f;
static float s_pitch = 0.0f;
static float s_yaw   = 0.0f;

// Zero-reference offsets set by gyroIMUZero()
static float s_rollRef  = 0.0f;
static float s_pitchRef = 0.0f;
static float s_yawRef   = 0.0f;

static unsigned long s_lastUs = 0;
static bool s_initialised = false;

// ── I2C helpers ───────────────────────────────────────────────────────────────

static bool imuWriteReg(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(GYRO_IMU_ADDR);
    Wire.write(reg);
    Wire.write(val);
    return Wire.endTransmission() == 0;
}

static bool imuReadRegs(uint8_t reg, uint8_t* buf, uint8_t len) {
    Wire.beginTransmission(GYRO_IMU_ADDR);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    uint8_t received = Wire.requestFrom((uint8_t)GYRO_IMU_ADDR, len);
    if (received != len) return false;
    for (uint8_t i = 0; i < len; i++) buf[i] = Wire.read();
    return true;
}

// ── Initialisation ────────────────────────────────────────────────────────────

bool gyroIMUInit() {
    // Wire.begin() already called by gyroTouchInit(); just verify the bus.
    uint8_t who = 0;
    if (!imuReadRegs(QMI_WHO_AM_I, &who, 1) || who != QMI_WHO_AM_I_VAL) {
        if (Serial) {
            Serial.print(F("[IMU] QMI8658 not found. WHO_AM_I=0x"));
            Serial.println(who, HEX);
        }
        return false;
    }
    if (Serial) Serial.println(F("[IMU] QMI8658 found"));

    // Disable all outputs before reconfiguring
    imuWriteReg(QMI_CTRL7, 0x00);

    // CTRL1: I2C, address auto-increment, little-endian output (default 0x40)
    imuWriteReg(QMI_CTRL1, 0x40);

    // Configure accel and gyro
    imuWriteReg(QMI_CTRL2, QMI_CTRL2_VAL);
    imuWriteReg(QMI_CTRL3, QMI_CTRL3_VAL);

    // Enable accel (bit0) + gyro (bit1)
    imuWriteReg(QMI_CTRL7, 0x03);

    delay(10);  // allow first samples to be ready

    s_roll = 0.0f; s_pitch = 0.0f; s_yaw = 0.0f;
    s_rollRef = 0.0f; s_pitchRef = 0.0f; s_yawRef = 0.0f;
    s_lastUs  = micros();
    s_initialised = true;
    return true;
}

// ── Read + complementary filter ───────────────────────────────────────────────

bool gyroIMURead(float* roll, float* pitch, float* yaw) {
    if (!s_initialised) return false;

    // Burst-read 12 bytes: ACCEL_X_L..GYRO_Z_H
    uint8_t buf[12];
    if (!imuReadRegs(QMI_ACCEL_X_L, buf, 12)) return false;

    // Reconstruct signed 16-bit values (little-endian register layout)
    int16_t rawAx = (int16_t)((buf[1]  << 8) | buf[0]);
    int16_t rawAy = (int16_t)((buf[3]  << 8) | buf[2]);
    int16_t rawAz = (int16_t)((buf[5]  << 8) | buf[4]);
    int16_t rawGx = (int16_t)((buf[7]  << 8) | buf[6]);
    int16_t rawGy = (int16_t)((buf[9]  << 8) | buf[8]);
    int16_t rawGz = (int16_t)((buf[11] << 8) | buf[10]);

    // Convert to physical units
    float ax = (float)rawAx / ACCEL_SENS;  // g
    float ay = (float)rawAy / ACCEL_SENS;
    float az = (float)rawAz / ACCEL_SENS;
    float gx = (float)rawGx / GYRO_SENS;   // deg/s
    float gy = (float)rawGy / GYRO_SENS;
    float gz = (float)rawGz / GYRO_SENS;

    // Time delta in seconds
    unsigned long nowUs = micros();
    float dt = (float)(nowUs - s_lastUs) * 1e-6f;
    s_lastUs = nowUs;
    // Clamp dt: ignore unreasonably large gaps (e.g. after Serial pause)
    if (dt <= 0.0f || dt > 0.5f) dt = 0.01f;

    // Accelerometer-derived roll and pitch (stable long-term, noisy short-term)
    float accelRoll  = atan2f(ay, az) * IMU_RAD2DEG;
    float accelPitch = atan2f(-ax, sqrtf(ay * ay + az * az)) * IMU_RAD2DEG;

    // Complementary filter: 98% gyro integration + 2% accelerometer correction
    s_roll  = CF_ALPHA * (s_roll  + gx * dt) + (1.0f - CF_ALPHA) * accelRoll;
    s_pitch = CF_ALPHA * (s_pitch + gy * dt) + (1.0f - CF_ALPHA) * accelPitch;

    // Yaw: gyro-only (no magnetometer); accumulates drift over time.
    s_yaw += gz * dt;
    // Wrap yaw to [−180, +180]
    while (s_yaw >  180.0f) s_yaw -= 360.0f;
    while (s_yaw < -180.0f) s_yaw += 360.0f;

    // Apply zero reference
    *roll  = s_roll  - s_rollRef;
    *pitch = s_pitch - s_pitchRef;
    *yaw   = s_yaw   - s_yawRef;

    // Wrap output roll/yaw to [−180, +180]
    while (*roll  >  180.0f) *roll  -= 360.0f;
    while (*roll  < -180.0f) *roll  += 360.0f;
    while (*yaw   >  180.0f) *yaw   -= 360.0f;
    while (*yaw   < -180.0f) *yaw   += 360.0f;

    return true;
}

// #776 — diagnostic-firmware accessor. Mirrors gyroIMURead() (same chip
// read, same CF advance) but also fills out the raw chip counts so a
// test firmware can show the operator what axes the hardware is actually
// reporting before any convention/zeroing/filter masks the picture.
bool gyroIMUReadRaw(GyroImuRaw* out) {
    if (!s_initialised || !out) return false;
    uint8_t buf[12];
    if (!imuReadRegs(QMI_ACCEL_X_L, buf, 12)) return false;

    int16_t rawAx = (int16_t)((buf[1]  << 8) | buf[0]);
    int16_t rawAy = (int16_t)((buf[3]  << 8) | buf[2]);
    int16_t rawAz = (int16_t)((buf[5]  << 8) | buf[4]);
    int16_t rawGx = (int16_t)((buf[7]  << 8) | buf[6]);
    int16_t rawGy = (int16_t)((buf[9]  << 8) | buf[8]);
    int16_t rawGz = (int16_t)((buf[11] << 8) | buf[10]);

    float ax = (float)rawAx / ACCEL_SENS;
    float ay = (float)rawAy / ACCEL_SENS;
    float az = (float)rawAz / ACCEL_SENS;
    float gx = (float)rawGx / GYRO_SENS;
    float gy = (float)rawGy / GYRO_SENS;
    float gz = (float)rawGz / GYRO_SENS;

    unsigned long nowUs = micros();
    float dt = (float)(nowUs - s_lastUs) * 1e-6f;
    s_lastUs = nowUs;
    if (dt <= 0.0f || dt > 0.5f) dt = 0.01f;

    float accelRoll  = atan2f(ay, az) * IMU_RAD2DEG;
    float accelPitch = atan2f(-ax, sqrtf(ay * ay + az * az)) * IMU_RAD2DEG;

    s_roll  = CF_ALPHA * (s_roll  + gx * dt) + (1.0f - CF_ALPHA) * accelRoll;
    s_pitch = CF_ALPHA * (s_pitch + gy * dt) + (1.0f - CF_ALPHA) * accelPitch;
    s_yaw  += gz * dt;
    while (s_yaw >  180.0f) s_yaw -= 360.0f;
    while (s_yaw < -180.0f) s_yaw += 360.0f;

    out->rawAx = rawAx; out->rawAy = rawAy; out->rawAz = rawAz;
    out->rawGx = rawGx; out->rawGy = rawGy; out->rawGz = rawGz;
    out->accelG[0]  = ax; out->accelG[1]  = ay; out->accelG[2]  = az;
    out->gyroDps[0] = gx; out->gyroDps[1] = gy; out->gyroDps[2] = gz;
    out->absoluteEulerDeg[0] = s_roll;
    out->absoluteEulerDeg[1] = s_pitch;
    out->absoluteEulerDeg[2] = s_yaw;
    float fr = s_roll - s_rollRef, fp = s_pitch - s_pitchRef, fy = s_yaw - s_yawRef;
    while (fr >  180.0f) fr -= 360.0f;
    while (fr < -180.0f) fr += 360.0f;
    while (fy >  180.0f) fy -= 360.0f;
    while (fy < -180.0f) fy += 360.0f;
    out->filteredEulerDeg[0] = fr;
    out->filteredEulerDeg[1] = fp;
    out->filteredEulerDeg[2] = fy;
    out->dtSec = dt;
    return true;
}

// ── Calibration ──────────────────────────────────────────────────────────────

void gyroIMUZero() {
    s_rollRef  = s_roll;
    s_pitchRef = s_pitch;
    s_yawRef   = s_yaw;
}

#endif  // BOARD_GYRO
