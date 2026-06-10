#pragma once
/**
 * foc_pmsm.h — PMSM/BLDC Field-Oriented Control (STM32G4 HAL)
 * Golden Module: MotorDriveForge
 *
 * 하드웨어 비종속: PWM/엔코더 핸들과 채널을 구조체로 주입받는다(glue가 채움).
 * 전류 측정은 외부(ADC+OPAMP)에서 읽어 [A] 단위로 FOC_CurrentLoop에 넘긴다.
 * 알고리즘(Clarke/Park/SVPWM, PI)은 표준 공개 수식 기반 클린룸 구현.
 *
 * 사용 흐름:
 *   FOC_Init(&foc);  FOC_Start(&foc);
 *   [전류 루프 ISR, 예: PWM update]  ia,ib,ic 측정 → FOC_CurrentLoop(&foc, ia,ib,ic, dt_i);
 *   [속도 루프, 느린 주기]            speed 측정 → FOC_SpeedLoop(&foc, speed, dt_s);
 */
#include "stm32g4xx_hal.h"
#include <stdint.h>
#include <stdbool.h>

/* ── PI 제어기 (anti-windup 클램프) ───────────────────────────────────────── */
typedef struct {
    float Kp;
    float Ki;
    float integral;
    float out_min;
    float out_max;
} FOC_PI;

void  FOC_PI_Init(FOC_PI *pi, float Kp, float Ki, float out_min, float out_max);
float FOC_PI_Step(FOC_PI *pi, float error, float dt);
void  FOC_PI_Reset(FOC_PI *pi);

/* ── FOC 인스턴스 ─────────────────────────────────────────────────────────── */
typedef struct {
    /* PWM: 3상 상보 출력 가능한 고급 타이머(TIM1/TIM8) */
    TIM_HandleTypeDef *htim_pwm;
    uint32_t  ch_u;            /* TIM_CHANNEL_1 등 */
    uint32_t  ch_v;
    uint32_t  ch_w;
    uint32_t  pwm_arr;         /* 타이머 ARR(주기) — 듀티 0..1 → 0..ARR */

    /* 위치 피드백: 엔코더 타이머(센서리스/홀이면 NULL, 외부서 theta_e 직접 설정) */
    TIM_HandleTypeDef *htim_enc;
    uint16_t  enc_cpr;         /* 엔코더 4체배 counts/rev */
    uint8_t   pole_pairs;      /* 극쌍수 */

    /* 전류/속도 PI */
    FOC_PI    pi_id;           /* d축 전류 (SPMSM은 ref=0) */
    FOC_PI    pi_iq;           /* q축 전류 (토크) */
    FOC_PI    pi_speed;        /* 속도 → iq_ref */

    float     vdc;             /* DC 버스 전압[V] (SVPWM 정규화) */
    float     id_ref;          /* [A] */
    float     iq_ref;          /* [A] (속도 루프가 갱신) */
    float     speed_ref;       /* [rad/s] (전기각속도) */

    /* 내부 상태 */
    float     theta_e;         /* 전기각[rad] */
    int32_t   enc_offset;      /* 정렬 오프셋(카운트) */
    bool      running;
} FOC_TypeDef;

void  FOC_Init(FOC_TypeDef *foc);
void  FOC_Start(FOC_TypeDef *foc);
void  FOC_Stop(FOC_TypeDef *foc);

/* 엔코더 카운트 → 전기각 갱신(htim_enc 있을 때). 반환: theta_e[rad] */
float FOC_UpdateAngleFromEncoder(FOC_TypeDef *foc);

/* 전류 루프 1스텝 — 측정 상전류[A] + 현재 theta_e로 SVPWM 듀티 적용 */
void  FOC_CurrentLoop(FOC_TypeDef *foc, float ia, float ib, float ic, float dt);

/* 속도 루프 1스텝 — 측정 전기각속도[rad/s] → iq_ref 갱신 */
void  FOC_SpeedLoop(FOC_TypeDef *foc, float speed_meas, float dt);

/* 지령 설정 헬퍼 */
void  FOC_SetSpeedRef(FOC_TypeDef *foc, float speed_ref);
void  FOC_SetTorqueRef(FOC_TypeDef *foc, float iq_ref);   /* 토크(전류)직접 모드 */
