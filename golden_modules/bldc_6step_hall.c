/**
 * bldc_6step_hall.c — Hall 인터럽트 기반 6-Step BLDC 코뮤테이션
 * Golden Module: MotorDriveForge
 */
#include "bldc_6step_hall.h"

/* ── 6-Step 코뮤테이션 테이블 ────────────────────────────────────────────
 * Hall 상태 (CBA 비트) → 활성 채널 페어
 * 형식: {CH1(H/L), CH2(H/L), CH3(H/L)} — 1=ON, 0=OFF
 *
 * 정방향 (CW): Hall 상태 1~6
 * hall_state[hall_code][phase] : phase 0~5 = CH1H,CH1L,CH2H,CH2L,CH3H,CH3L
 */
static const uint8_t COMM_TABLE_FWD[8][6] = {
    /* idx=0 */ {0, 0, 0, 0, 0, 0}, /* 유효하지 않은 상태 */
    /* idx=1 */ {1, 0, 0, 1, 0, 0}, /* Hall=001: U+ V- */
    /* idx=2 */ {0, 0, 0, 1, 1, 0}, /* Hall=010: V+ W- */  /* 수정 필요 시 칩 데이터시트 참조 */
    /* idx=3 */ {1, 0, 0, 0, 0, 1}, /* Hall=011: U+ W- */
    /* idx=4 */ {0, 1, 1, 0, 0, 0}, /* Hall=100: V+ U- (실제 W+ U-) */
    /* idx=5 */ {0, 0, 1, 0, 0, 1}, /* Hall=101: W+ V- */  /* 모터에 따라 조정 */
    /* idx=6 */ {0, 1, 0, 0, 1, 0}, /* Hall=110: W+ U- */
    /* idx=7 */ {0, 0, 0, 0, 0, 0}, /* 유효하지 않은 상태 */
};

/* 역방향: FWD 테이블에서 H/L을 스왑 */
static const uint8_t COMM_TABLE_REV[8][6] = {
    /* idx=0 */ {0, 0, 0, 0, 0, 0},
    /* idx=1 */ {0, 1, 1, 0, 0, 0},
    /* idx=2 */ {1, 0, 1, 0, 0, 0},
    /* idx=3 */ {0, 1, 0, 0, 1, 0},
    /* idx=4 */ {1, 0, 0, 1, 0, 0},
    /* idx=5 */ {0, 0, 0, 1, 1, 0},
    /* idx=6 */ {1, 0, 0, 0, 0, 1},
    /* idx=7 */ {0, 0, 0, 0, 0, 0},
};

static const uint32_t TIM_CHANNELS[3] = {
    TIM_CHANNEL_1, TIM_CHANNEL_2, TIM_CHANNEL_3
};

/* ── 내부 헬퍼 ─────────────────────────────────────────────────────────── */

static uint8_t read_hall(BLDC_TypeDef *bldc)
{
    uint8_t a = (HAL_GPIO_ReadPin(bldc->hall_gpio, bldc->hall_pin_a) == GPIO_PIN_SET) ? 1 : 0;
    uint8_t b = (HAL_GPIO_ReadPin(bldc->hall_gpio, bldc->hall_pin_b) == GPIO_PIN_SET) ? 1 : 0;
    uint8_t c = (HAL_GPIO_ReadPin(bldc->hall_gpio, bldc->hall_pin_c) == GPIO_PIN_SET) ? 1 : 0;
    return (uint8_t)((c << 2) | (b << 1) | a); /* CBA 비트 순서 */
}

static void apply_commutation(BLDC_TypeDef *bldc, uint8_t hall)
{
    const uint8_t *table = (bldc->direction >= 0)
                           ? COMM_TABLE_FWD[hall]
                           : COMM_TABLE_REV[hall];

    uint32_t period = bldc->htim_pwm->Instance->ARR;
    uint32_t pulse  = (uint32_t)(bldc->duty * (float)period);

    for (uint8_t ph = 0; ph < 3; ph++) {
        uint8_t hi = table[ph * 2];
        uint8_t lo = table[ph * 2 + 1];

        if (hi) {
            __HAL_TIM_SET_COMPARE(bldc->htim_pwm, TIM_CHANNELS[ph], pulse);
        } else if (lo) {
            /* 하위 스위치: 보완 채널을 100% 켜는 방식 */
            __HAL_TIM_SET_COMPARE(bldc->htim_pwm, TIM_CHANNELS[ph], 0);
        } else {
            /* 비활성 채널: 상보 모두 OFF */
            __HAL_TIM_SET_COMPARE(bldc->htim_pwm, TIM_CHANNELS[ph], 0);
        }
    }
    bldc->commutation_count++;
}

static void all_channels_off(BLDC_TypeDef *bldc)
{
    for (uint8_t ph = 0; ph < 3; ph++) {
        __HAL_TIM_SET_COMPARE(bldc->htim_pwm, TIM_CHANNELS[ph], 0);
        HAL_TIMEx_PWMN_Stop(bldc->htim_pwm, TIM_CHANNELS[ph]);
    }
}

/* ── 공개 API ───────────────────────────────────────────────────────────── */

void BLDC_Init(BLDC_TypeDef *bldc)
{
    bldc->state             = BLDC_STATE_STOPPED;
    bldc->direction         = 1;
    bldc->duty              = 0.0f;
    bldc->last_hall_tick    = 0;
    bldc->hall_period_us    = 0;
    bldc->speed_rpm         = 0;
    bldc->pole_pairs        = (bldc->pole_pairs == 0) ? 4 : bldc->pole_pairs;
    bldc->commutation_count = 0;
    bldc->fault_count       = 0;

    /* PWM 채널 시작 (듀티 0) */
    for (uint8_t ph = 0; ph < 3; ph++) {
        HAL_TIM_PWM_Start(bldc->htim_pwm, TIM_CHANNELS[ph]);
        HAL_TIMEx_PWMN_Start(bldc->htim_pwm, TIM_CHANNELS[ph]);
        __HAL_TIM_SET_COMPARE(bldc->htim_pwm, TIM_CHANNELS[ph], 0);
    }
    /* 속도 측정 타이머 시작 */
    HAL_TIM_Base_Start(bldc->htim_speed);
}

void BLDC_Start(BLDC_TypeDef *bldc, int8_t direction, float duty)
{
    if (bldc->state == BLDC_STATE_FAULT) return;
    bldc->direction = direction;
    bldc->duty      = (duty > 1.0f) ? 1.0f : (duty < 0.0f ? 0.0f : duty);
    bldc->state     = BLDC_STATE_RUNNING;
    /* 현재 Hall 상태로 즉시 코뮤테이션 */
    apply_commutation(bldc, read_hall(bldc));
}

void BLDC_Stop(BLDC_TypeDef *bldc)
{
    bldc->state = BLDC_STATE_STOPPED;
    bldc->duty  = 0.0f;
    all_channels_off(bldc);
}

void BLDC_SetSpeed(BLDC_TypeDef *bldc, float duty)
{
    bldc->duty = (duty > 1.0f) ? 1.0f : (duty < 0.0f ? 0.0f : duty);
}

int16_t BLDC_GetSpeed(BLDC_TypeDef *bldc)
{
    return bldc->speed_rpm;
}

void BLDC_HallISR(BLDC_TypeDef *bldc)
{
    if (bldc->state != BLDC_STATE_RUNNING) return;

    /* 속도 계산 */
    uint32_t now = __HAL_TIM_GET_COUNTER(bldc->htim_speed);
    if (bldc->last_hall_tick != 0) {
        uint32_t diff = (now >= bldc->last_hall_tick)
                        ? (now - bldc->last_hall_tick)
                        : (0xFFFFFFFFU - bldc->last_hall_tick + now + 1);
        bldc->hall_period_us = diff; /* 타이머가 1us 분해능이라 가정 */
        if (diff > 0) {
            /* RPM = 60e6 / (hall_period_us * 6 * pole_pairs) */
            bldc->speed_rpm = (int16_t)(10000000UL /
                               (diff * 6UL * (uint32_t)bldc->pole_pairs));
            if (bldc->direction < 0) bldc->speed_rpm = -bldc->speed_rpm;
        }
    }
    bldc->last_hall_tick = now;

    apply_commutation(bldc, read_hall(bldc));
}

void BLDC_BrkISR(BLDC_TypeDef *bldc)
{
    /* TIM1 BRK: 하드웨어가 이미 출력 차단 — 상태만 업데이트 */
    bldc->state = BLDC_STATE_FAULT;
    bldc->fault_count++;
    bldc->speed_rpm = 0;
    bldc->duty      = 0.0f;
}

void BLDC_ClearFault(BLDC_TypeDef *bldc)
{
    if (bldc->state != BLDC_STATE_FAULT) return;
    /* TIM1 BDTR: MOE 재활성화 */
    bldc->htim_pwm->Instance->BDTR |= TIM_BDTR_MOE;
    bldc->state = BLDC_STATE_STOPPED;
}
