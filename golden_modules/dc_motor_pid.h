#pragma once
/**
 * dc_motor_pid.h — DC 모터 H-bridge PWM + PID 제어 (STM32G4 HAL)
 * Golden Module: MotorDriveForge
 */
#include "stm32g4xx_hal.h"
#include <stdint.h>
#include <stdbool.h>

/* ── PID ────────────────────────────────────────────────────────────────── */
typedef struct {
    float Kp;
    float Ki;
    float Kd;
    float dt;           /* 제어 주기 [s] */
    float integral;
    float prev_error;
    float output_min;
    float output_max;
    float integral_max; /* windup 방지 클램프 */
} PID_TypeDef;

void  PID_Init(PID_TypeDef *pid, float Kp, float Ki, float Kd, float dt,
               float out_min, float out_max);
float PID_Compute(PID_TypeDef *pid, float setpoint, float measured);
void  PID_Reset(PID_TypeDef *pid);

/* ── DC Motor ───────────────────────────────────────────────────────────── */
typedef struct {
    TIM_HandleTypeDef *htim;
    uint32_t  ch_fwd;       /* TIM 채널 (정방향 PWM) */
    uint32_t  ch_rev;       /* TIM 채널 (역방향 PWM) — 0이면 단방향 */
    GPIO_TypeDef *dir_gpio; /* 방향 GPIO 포트 (NULL이면 미사용) */
    uint16_t  dir_pin;
    uint32_t  period;       /* TIM ARR 값 (0~period → 0~100%) */
    float     deadzone_pct; /* 데드존 [%] (-deadzone ~ +deadzone → 0) */
} DCMotor_TypeDef;

void  DCMotor_Init(DCMotor_TypeDef *motor);
void  DCMotor_SetDuty(DCMotor_TypeDef *motor, float duty_pct); /* -100 ~ +100 */
void  DCMotor_Stop(DCMotor_TypeDef *motor);
void  DCMotor_Coast(DCMotor_TypeDef *motor); /* 자유 회전 (PWM 모두 0) */
