/**
 * foc_pmsm.c — PMSM/BLDC Field-Oriented Control (STM32G4 HAL)
 * Golden Module: MotorDriveForge
 *
 * 표준 FOC 파이프라인:
 *   상전류(ia,ib,ic) ─ Clarke → (iα,iβ) ─ Park(θe) → (id,iq)
 *   PI(id_ref-id), PI(iq_ref-iq) → (vd,vq) ─ inv.Park(θe) → (vα,vβ)
 *   ─ SVPWM(min-max 주입) → 3상 듀티 → TIM CCR
 * 알고리즘은 공개 표준 수식 기반 클린룸 구현(특정 GPL 코드 미차용).
 */
#include "foc_pmsm.h"
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979f
#endif
#define FOC_SQRT3      1.7320508f
#define FOC_SQRT3_2    0.8660254f

/* ── PI ───────────────────────────────────────────────────────────────────── */
void FOC_PI_Init(FOC_PI *pi, float Kp, float Ki, float out_min, float out_max)
{
    pi->Kp = Kp; pi->Ki = Ki;
    pi->integral = 0.0f;
    pi->out_min = out_min; pi->out_max = out_max;
}

void FOC_PI_Reset(FOC_PI *pi) { pi->integral = 0.0f; }

float FOC_PI_Step(FOC_PI *pi, float error, float dt)
{
    float out = pi->Kp * error + pi->integral;
    /* anti-windup: 출력이 포화면 적분 누적 보류 */
    if (out > pi->out_max) {
        out = pi->out_max;
    } else if (out < pi->out_min) {
        out = pi->out_min;
    } else {
        pi->integral += pi->Ki * error * dt;
    }
    return out;
}

/* ── 라이프사이클 ─────────────────────────────────────────────────────────── */
void FOC_Init(FOC_TypeDef *foc)
{
    foc->theta_e = 0.0f;
    foc->iq_ref = 0.0f;
    foc->id_ref = 0.0f;
    foc->running = false;
    FOC_PI_Reset(&foc->pi_id);
    FOC_PI_Reset(&foc->pi_iq);
    FOC_PI_Reset(&foc->pi_speed);
}

void FOC_Start(FOC_TypeDef *foc)
{
    if (!foc->htim_pwm) return;
    HAL_TIM_PWM_Start(foc->htim_pwm, foc->ch_u);
    HAL_TIM_PWM_Start(foc->htim_pwm, foc->ch_v);
    HAL_TIM_PWM_Start(foc->htim_pwm, foc->ch_w);
    HAL_TIMEx_PWMN_Start(foc->htim_pwm, foc->ch_u);
    HAL_TIMEx_PWMN_Start(foc->htim_pwm, foc->ch_v);
    HAL_TIMEx_PWMN_Start(foc->htim_pwm, foc->ch_w);
    if (foc->htim_enc) {
        HAL_TIM_Encoder_Start(foc->htim_enc, TIM_CHANNEL_ALL);
    }
    foc->running = true;
}

void FOC_Stop(FOC_TypeDef *foc)
{
    if (!foc->htim_pwm) return;
    __HAL_TIM_SET_COMPARE(foc->htim_pwm, foc->ch_u, 0);
    __HAL_TIM_SET_COMPARE(foc->htim_pwm, foc->ch_v, 0);
    __HAL_TIM_SET_COMPARE(foc->htim_pwm, foc->ch_w, 0);
    HAL_TIM_PWM_Stop(foc->htim_pwm, foc->ch_u);
    HAL_TIM_PWM_Stop(foc->htim_pwm, foc->ch_v);
    HAL_TIM_PWM_Stop(foc->htim_pwm, foc->ch_w);
    HAL_TIMEx_PWMN_Stop(foc->htim_pwm, foc->ch_u);
    HAL_TIMEx_PWMN_Stop(foc->htim_pwm, foc->ch_v);
    HAL_TIMEx_PWMN_Stop(foc->htim_pwm, foc->ch_w);
    foc->running = false;
}

/* ── 엔코더 → 전기각 ──────────────────────────────────────────────────────── */
float FOC_UpdateAngleFromEncoder(FOC_TypeDef *foc)
{
    if (!foc->htim_enc || foc->enc_cpr == 0) return foc->theta_e;
    int32_t cnt = (int32_t)__HAL_TIM_GET_COUNTER(foc->htim_enc) - foc->enc_offset;
    /* 기계각[rev] → 전기각[rad], 0..2π로 래핑 */
    float mech_rev = (float)cnt / (float)foc->enc_cpr;
    float theta = mech_rev * (float)foc->pole_pairs * 2.0f * M_PI;
    theta = fmodf(theta, 2.0f * M_PI);
    if (theta < 0.0f) theta += 2.0f * M_PI;
    foc->theta_e = theta;
    return theta;
}

/* ── SVPWM (min-max 공통모드 주입) ────────────────────────────────────────── */
/* vα,vβ[V] → 3상 듀티(0..1). vdc로 정규화, 듀티는 0..1 클램프. */
static void foc_svpwm(float valpha, float vbeta, float vdc,
                      float *du, float *dv, float *dw)
{
    if (vdc < 1e-3f) vdc = 1e-3f;
    /* 역 Clarke: 상전압 */
    float va = valpha;
    float vb = -0.5f * valpha + FOC_SQRT3_2 * vbeta;
    float vc = -0.5f * valpha - FOC_SQRT3_2 * vbeta;
    /* 공통모드(min+max)/2 제거 → 선간전압 보존하며 이용률 ↑ */
    float vmax = va, vmin = va;
    if (vb > vmax) vmax = vb;
    if (vc > vmax) vmax = vc;
    if (vb < vmin) vmin = vb;
    if (vc < vmin) vmin = vc;
    float vcom = 0.5f * (vmax + vmin);
    float inv = 1.0f / vdc;
    float a = (va - vcom) * inv + 0.5f;
    float b = (vb - vcom) * inv + 0.5f;
    float c = (vc - vcom) * inv + 0.5f;
    /* 0..1 클램프 */
    *du = a < 0.0f ? 0.0f : (a > 1.0f ? 1.0f : a);
    *dv = b < 0.0f ? 0.0f : (b > 1.0f ? 1.0f : b);
    *dw = c < 0.0f ? 0.0f : (c > 1.0f ? 1.0f : c);
}

/* ── 전류 루프 ────────────────────────────────────────────────────────────── */
void FOC_CurrentLoop(FOC_TypeDef *foc, float ia, float ib, float ic, float dt)
{
    if (!foc->running || !foc->htim_pwm) return;
    (void)ic;  /* ia+ib+ic≈0 가정 → ia,ib만 사용(측정 노이즈에 강함) */

    /* Clarke */
    float ialpha = ia;
    float ibeta  = (ia + 2.0f * ib) / FOC_SQRT3;

    /* Park */
    float s = sinf(foc->theta_e);
    float co = cosf(foc->theta_e);
    float id =  ialpha * co + ibeta * s;
    float iq = -ialpha * s  + ibeta * co;

    /* 전류 PI */
    float vd = FOC_PI_Step(&foc->pi_id, foc->id_ref - id, dt);
    float vq = FOC_PI_Step(&foc->pi_iq, foc->iq_ref - iq, dt);

    /* inverse Park */
    float valpha = vd * co - vq * s;
    float vbeta  = vd * s  + vq * co;

    /* SVPWM → 듀티 → CCR */
    float du, dv, dw;
    foc_svpwm(valpha, vbeta, foc->vdc, &du, &dv, &dw);
    uint32_t arr = foc->pwm_arr;
    __HAL_TIM_SET_COMPARE(foc->htim_pwm, foc->ch_u, (uint32_t)(du * arr));
    __HAL_TIM_SET_COMPARE(foc->htim_pwm, foc->ch_v, (uint32_t)(dv * arr));
    __HAL_TIM_SET_COMPARE(foc->htim_pwm, foc->ch_w, (uint32_t)(dw * arr));
}

/* ── 속도 루프 ────────────────────────────────────────────────────────────── */
void FOC_SpeedLoop(FOC_TypeDef *foc, float speed_meas, float dt)
{
    if (!foc->running) return;
    foc->iq_ref = FOC_PI_Step(&foc->pi_speed, foc->speed_ref - speed_meas, dt);
}

void FOC_SetSpeedRef(FOC_TypeDef *foc, float speed_ref) { foc->speed_ref = speed_ref; }
void FOC_SetTorqueRef(FOC_TypeDef *foc, float iq_ref)   { foc->iq_ref = iq_ref; }
