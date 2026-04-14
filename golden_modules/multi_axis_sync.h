#pragma once
/**
 * multi_axis_sync.h — TIM1/TIM8/TIM20 PWM 동기화 (멀티모터 FOC)
 * 설계: TIM1 마스터 → TIM8/TIM20 슬레이브 (TRGO → ITR)
 * ADC 트리거 타이밍 일치를 위한 중앙 정렬 동기화 필수
 *
 * Golden Module: MotorDriveForge
 */
#include "stm32g4xx_hal.h"
#include <stdint.h>

#define MULTI_AXIS_MAX_SLAVES 3

typedef struct {
    TIM_HandleTypeDef *master_htim;
    TIM_HandleTypeDef *slave_htim[MULTI_AXIS_MAX_SLAVES];
    uint8_t            slave_count;
    uint8_t            pwm_channels[MULTI_AXIS_MAX_SLAVES + 1][6]; /* [axis][ch0..ch5] */
} MultiAxisSync_TypeDef;

/**
 * @brief  마스터/슬레이브 타이머 동기화 초기화
 *         - TIM1 TRGO(Update) → TIM8 ITR0, TIM20 ITR0 연결
 *         - 중앙 정렬 모드 확인 (Center-Aligned 1)
 */
void MultiAxisSync_Init(MultiAxisSync_TypeDef *sync,
                        TIM_HandleTypeDef *master,
                        TIM_HandleTypeDef *slaves[],
                        uint8_t slave_count);

/** @brief 마스터 시작 → 슬레이브 동시 활성화 */
void MultiAxisSync_Start(MultiAxisSync_TypeDef *sync);

/** @brief 안전 순서로 정지 (슬레이브 먼저, 마스터 나중) */
void MultiAxisSync_Stop(MultiAxisSync_TypeDef *sync);

/**
 * @brief  개별 축 PWM 듀티 설정
 * @param  axis  0=마스터(TIM1), 1..N=슬레이브
 * @param  duty  0.0 ~ 1.0
 */
void MultiAxisSync_SetPWM(MultiAxisSync_TypeDef *sync, uint8_t axis, float duty);

/** @brief 동기화 상태 확인 (슬레이브 카운터가 마스터와 일치하는지) */
uint8_t MultiAxisSync_IsLocked(MultiAxisSync_TypeDef *sync);
