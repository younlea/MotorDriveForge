/**
 * dc_motor_pid.c — DC 모터 H-bridge PWM + PID 제어 (STM32G4 HAL)
 * Golden Module: MotorDriveForge
 */
#include "dc_motor_pid.h"
#include <math.h>

/* ── PID ────────────────────────────────────────────────────────────────── */

void PID_Init(PID_TypeDef *pid, float Kp, float Ki, float Kd, float dt,
              float out_min, float out_max)
{
    pid->Kp          = Kp;
    pid->Ki          = Ki;
    pid->Kd          = Kd;
    pid->dt          = dt;
    pid->integral    = 0.0f;
    pid->prev_error  = 0.0f;
    pid->output_min  = out_min;
    pid->output_max  = out_max;
    /* integral 클램프: 출력 범위와 동일 비율 */
    pid->integral_max = (out_max - out_min) / (Ki > 0.0f ? Ki : 1.0f);
}

float PID_Compute(PID_TypeDef *pid, float setpoint, float measured)
{
    float error      = setpoint - measured;
    float derivative = (error - pid->prev_error) / pid->dt;

    pid->integral += error * pid->dt;

    /* Anti-windup: integral 클램프 */
    if (pid->integral >  pid->integral_max) pid->integral =  pid->integral_max;
    if (pid->integral < -pid->integral_max) pid->integral = -pid->integral_max;

    float output = pid->Kp * error
                 + pid->Ki * pid->integral
                 + pid->Kd * derivative;

    /* 출력 클램프 */
    if (output >  pid->output_max) output =  pid->output_max;
    if (output <  pid->output_min) output =  pid->output_min;

    pid->prev_error = error;
    return output;
}

void PID_Reset(PID_TypeDef *pid)
{
    pid->integral   = 0.0f;
    pid->prev_error = 0.0f;
}

/* ── DC Motor ───────────────────────────────────────────────────────────── */

void DCMotor_Init(DCMotor_TypeDef *motor)
{
    /* PWM 시작 */
    HAL_TIM_PWM_Start(motor->htim, motor->ch_fwd);
    if (motor->ch_rev != 0) {
        HAL_TIM_PWM_Start(motor->htim, motor->ch_rev);
    }
    DCMotor_Stop(motor);
}

void DCMotor_SetDuty(DCMotor_TypeDef *motor, float duty_pct)
{
    /* 데드존 처리 */
    if (fabsf(duty_pct) < motor->deadzone_pct) {
        DCMotor_Stop(motor);
        return;
    }

    /* -100 ~ +100 범위 클램프 */
    if (duty_pct >  100.0f) duty_pct =  100.0f;
    if (duty_pct < -100.0f) duty_pct = -100.0f;

    uint32_t pulse = (uint32_t)(fabsf(duty_pct) * (float)motor->period / 100.0f);

    if (duty_pct > 0.0f) {
        /* 정방향 */
        if (motor->dir_gpio) {
            HAL_GPIO_WritePin(motor->dir_gpio, motor->dir_pin, GPIO_PIN_SET);
        }
        __HAL_TIM_SET_COMPARE(motor->htim, motor->ch_fwd, pulse);
        if (motor->ch_rev != 0) {
            __HAL_TIM_SET_COMPARE(motor->htim, motor->ch_rev, 0);
        }
    } else {
        /* 역방향 */
        if (motor->dir_gpio) {
            HAL_GPIO_WritePin(motor->dir_gpio, motor->dir_pin, GPIO_PIN_RESET);
            __HAL_TIM_SET_COMPARE(motor->htim, motor->ch_fwd, pulse);
        } else if (motor->ch_rev != 0) {
            __HAL_TIM_SET_COMPARE(motor->htim, motor->ch_fwd, 0);
            __HAL_TIM_SET_COMPARE(motor->htim, motor->ch_rev, pulse);
        }
    }
}

void DCMotor_Stop(DCMotor_TypeDef *motor)
{
    /* 브레이크: 양쪽 채널 100% (H-bridge 단락 제동) */
    __HAL_TIM_SET_COMPARE(motor->htim, motor->ch_fwd, motor->period);
    if (motor->ch_rev != 0) {
        __HAL_TIM_SET_COMPARE(motor->htim, motor->ch_rev, motor->period);
    }
}

void DCMotor_Coast(DCMotor_TypeDef *motor)
{
    /* 자유 회전: 양쪽 채널 0% */
    __HAL_TIM_SET_COMPARE(motor->htim, motor->ch_fwd, 0);
    if (motor->ch_rev != 0) {
        __HAL_TIM_SET_COMPARE(motor->htim, motor->ch_rev, 0);
    }
}
