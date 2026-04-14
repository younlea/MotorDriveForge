#pragma once
/**
 * bldc_6step_hall.h — Hall 인터럽트 기반 6-Step BLDC 코뮤테이션 (STM32G4 HAL)
 * Golden Module: MotorDriveForge
 *
 * 사용법:
 *   1. CubeMX에서 TIM1(6채널 상보 PWM) + 3개 EXTI(Hall A/B/C) 설정
 *   2. EXTI 핸들러에서 BLDC_HallISR() 호출
 *   3. TIM1 BRK 핸들러에서 BLDC_BrkISR() 호출
 */
#include "stm32g4xx_hal.h"
#include <stdint.h>
#include <stdbool.h>

typedef enum {
    BLDC_STATE_STOPPED = 0,
    BLDC_STATE_RUNNING,
    BLDC_STATE_FAULT,   /* BRK 트리거 */
} BLDC_State;

typedef struct {
    /* HAL 핸들 */
    TIM_HandleTypeDef *htim_pwm;   /* TIM1 or TIM8 — 6채널 상보 PWM */
    TIM_HandleTypeDef *htim_speed; /* Hall 펄스 간격 측정용 타이머 (32bit) */

    /* Hall GPIO */
    GPIO_TypeDef *hall_gpio;
    uint16_t      hall_pin_a;
    uint16_t      hall_pin_b;
    uint16_t      hall_pin_c;

    /* 상태 */
    BLDC_State state;
    int8_t     direction;   /* +1: 정방향, -1: 역방향 */
    float      duty;        /* 0.0 ~ 1.0 */

    /* 속도 측정 */
    uint32_t   last_hall_tick;   /* 마지막 Hall 펄스 타이머 카운트 */
    uint32_t   hall_period_us;   /* Hall 펄스 주기 [us] */
    int16_t    speed_rpm;
    uint8_t    pole_pairs;       /* 극 쌍 수 (기본 4) */

    /* 통계 */
    uint32_t   commutation_count;
    uint32_t   fault_count;
} BLDC_TypeDef;

void    BLDC_Init(BLDC_TypeDef *bldc);
void    BLDC_Start(BLDC_TypeDef *bldc, int8_t direction, float duty);
void    BLDC_Stop(BLDC_TypeDef *bldc);
void    BLDC_SetSpeed(BLDC_TypeDef *bldc, float duty); /* duty 0.0~1.0 */
int16_t BLDC_GetSpeed(BLDC_TypeDef *bldc);             /* RPM */

/** EXTI 핸들러에서 호출 */
void BLDC_HallISR(BLDC_TypeDef *bldc);

/** TIM1 BRK 인터럽트에서 호출 — 즉시 PWM 차단 */
void BLDC_BrkISR(BLDC_TypeDef *bldc);

/** Fault 해제 후 재시작 시도 */
void BLDC_ClearFault(BLDC_TypeDef *bldc);
