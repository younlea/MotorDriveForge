/**
 * multi_axis_sync.c — TIM1/TIM8/TIM20 PWM 동기화 (멀티모터 FOC)
 * Golden Module: MotorDriveForge
 *
 * 레지스터 직접 조작:
 *   TIMx->CR2  : MMS  (Master Mode Selection) → TRGO 출력
 *   TIMx->SMCR : SMS  (Slave Mode Selection) + TS (Trigger Source)
 */
#include "multi_axis_sync.h"

/* TIM1 TRGO → TIM8 ITR0, TIM20 ITR0
 * STM32G4 TRM Table 153: TIM8 ITR0 = TIM1, TIM20 ITR0 = TIM1 */
#define MASTER_MMS_UPDATE   (0x2U << TIM_CR2_MMS_Pos)  /* Update event → TRGO */
#define SLAVE_TS_ITR0       (0x0U << TIM_SMCR_TS_Pos)  /* ITR0 as trigger source */
#define SLAVE_SMS_TRIGGER   (0x6U << TIM_SMCR_SMS_Pos) /* Trigger mode (start on trigger) */

void MultiAxisSync_Init(MultiAxisSync_TypeDef *sync,
                        TIM_HandleTypeDef *master,
                        TIM_HandleTypeDef *slaves[],
                        uint8_t slave_count)
{
    sync->master_htim = master;
    sync->slave_count = (slave_count > MULTI_AXIS_MAX_SLAVES)
                        ? MULTI_AXIS_MAX_SLAVES : slave_count;

    for (uint8_t i = 0; i < sync->slave_count; i++) {
        sync->slave_htim[i] = slaves[i];
    }

    /* ── 마스터 TIM1: TRGO = Update event ─────────────────────────────── */
    /* CR2.MMS = 010 → Update event를 TRGO로 출력 */
    master->Instance->CR2 &= ~TIM_CR2_MMS_Msk;
    master->Instance->CR2 |= MASTER_MMS_UPDATE;

    /* ── 슬레이브 타이머: ITR0 트리거, 트리거 모드 ────────────────────── */
    for (uint8_t i = 0; i < sync->slave_count; i++) {
        TIM_TypeDef *tim = slaves[i]->Instance;

        /* SMCR: TS=000(ITR0), SMS=110(Trigger Mode) */
        tim->SMCR &= ~(TIM_SMCR_TS_Msk | TIM_SMCR_SMS_Msk);
        tim->SMCR |= SLAVE_TS_ITR0 | SLAVE_SMS_TRIGGER;

        /* 슬레이브 카운터를 마스터와 같은 값으로 프리셋 */
        tim->CNT = master->Instance->CNT;
    }
}

void MultiAxisSync_Start(MultiAxisSync_TypeDef *sync)
{
    /* 슬레이브 PWM 채널 먼저 시작 (카운터는 마스터 TRGO 대기) */
    for (uint8_t i = 0; i < sync->slave_count; i++) {
        TIM_HandleTypeDef *htim = sync->slave_htim[i];
        /* 6채널 상보 PWM 시작 */
        HAL_TIM_PWM_Start(htim, TIM_CHANNEL_1);
        HAL_TIMEx_PWMN_Start(htim, TIM_CHANNEL_1);
        HAL_TIM_PWM_Start(htim, TIM_CHANNEL_2);
        HAL_TIMEx_PWMN_Start(htim, TIM_CHANNEL_2);
        HAL_TIM_PWM_Start(htim, TIM_CHANNEL_3);
        HAL_TIMEx_PWMN_Start(htim, TIM_CHANNEL_3);
    }

    /* 마스터 TIM1 시작 → TRGO로 슬레이브 동시 활성화 */
    HAL_TIM_PWM_Start(sync->master_htim, TIM_CHANNEL_1);
    HAL_TIMEx_PWMN_Start(sync->master_htim, TIM_CHANNEL_1);
    HAL_TIM_PWM_Start(sync->master_htim, TIM_CHANNEL_2);
    HAL_TIMEx_PWMN_Start(sync->master_htim, TIM_CHANNEL_2);
    HAL_TIM_PWM_Start(sync->master_htim, TIM_CHANNEL_3);
    HAL_TIMEx_PWMN_Start(sync->master_htim, TIM_CHANNEL_3);
}

void MultiAxisSync_Stop(MultiAxisSync_TypeDef *sync)
{
    /* 슬레이브 먼저 정지 */
    for (uint8_t i = 0; i < sync->slave_count; i++) {
        TIM_HandleTypeDef *htim = sync->slave_htim[i];
        HAL_TIM_PWM_Stop(htim, TIM_CHANNEL_1);
        HAL_TIMEx_PWMN_Stop(htim, TIM_CHANNEL_1);
        HAL_TIM_PWM_Stop(htim, TIM_CHANNEL_2);
        HAL_TIMEx_PWMN_Stop(htim, TIM_CHANNEL_2);
        HAL_TIM_PWM_Stop(htim, TIM_CHANNEL_3);
        HAL_TIMEx_PWMN_Stop(htim, TIM_CHANNEL_3);
    }

    /* 마스터 정지 */
    HAL_TIM_PWM_Stop(sync->master_htim, TIM_CHANNEL_1);
    HAL_TIMEx_PWMN_Stop(sync->master_htim, TIM_CHANNEL_1);
    HAL_TIM_PWM_Stop(sync->master_htim, TIM_CHANNEL_2);
    HAL_TIMEx_PWMN_Stop(sync->master_htim, TIM_CHANNEL_2);
    HAL_TIM_PWM_Stop(sync->master_htim, TIM_CHANNEL_3);
    HAL_TIMEx_PWMN_Stop(sync->master_htim, TIM_CHANNEL_3);
}

void MultiAxisSync_SetPWM(MultiAxisSync_TypeDef *sync, uint8_t axis, float duty)
{
    if (duty < 0.0f) duty = 0.0f;
    if (duty > 1.0f) duty = 1.0f;

    TIM_HandleTypeDef *htim = (axis == 0)
        ? sync->master_htim
        : sync->slave_htim[axis - 1];

    if (!htim) return;

    uint32_t period = htim->Instance->ARR;
    uint32_t pulse  = (uint32_t)(duty * (float)period);

    /* 3상 동일 듀티 (FOC에서는 Park/Clarke 역변환 결과를 각 채널에 개별 설정) */
    __HAL_TIM_SET_COMPARE(htim, TIM_CHANNEL_1, pulse);
    __HAL_TIM_SET_COMPARE(htim, TIM_CHANNEL_2, pulse);
    __HAL_TIM_SET_COMPARE(htim, TIM_CHANNEL_3, pulse);
}

uint8_t MultiAxisSync_IsLocked(MultiAxisSync_TypeDef *sync)
{
    uint32_t master_cnt = sync->master_htim->Instance->CNT;
    uint32_t master_arr = sync->master_htim->Instance->ARR;

    for (uint8_t i = 0; i < sync->slave_count; i++) {
        uint32_t slave_cnt = sync->slave_htim[i]->Instance->CNT;
        /* 카운터 차이가 ARR의 5% 이내이면 동기화 양호 */
        uint32_t diff = (master_cnt > slave_cnt)
                        ? (master_cnt - slave_cnt)
                        : (slave_cnt - master_cnt);
        if (diff > master_arr / 20) {
            return 0; /* 동기화 실패 */
        }
    }
    return 1; /* 동기화 OK */
}
