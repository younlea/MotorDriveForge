#pragma once
/**
 * fdcan_motor_cmd.h — FDCAN 커맨드 파싱 (모터 제어 명령 수신/송신)
 * Golden Module: MotorDriveForge
 *
 * 설정: 1Mbps, Standard Frame ID
 * CAN ID 규칙:
 *   RX 커맨드: 0x100 + motor_idx (0x100~0x103)
 *   TX 상태:   0x200 + motor_idx (0x200~0x203)
 */
#include "stm32g4xx_hal.h"
#include <stdint.h>
#include <stdbool.h>

/* ── CAN ID 정의 ──────────────────────────────────────────────────────── */
#define FDCAN_CMD_BASE_ID     0x100U
#define FDCAN_STATUS_BASE_ID  0x200U
#define FDCAN_MAX_MOTORS      4U

/* ── 커맨드 타입 ────────────────────────────────────────────────────────── */
typedef enum {
    MOTOR_CMD_STOP           = 0x00,
    MOTOR_CMD_SET_SPEED      = 0x01,
    MOTOR_CMD_SET_DUTY       = 0x02,
    MOTOR_CMD_SET_DIRECTION  = 0x03,
    MOTOR_CMD_EMERGENCY_STOP = 0xFF,
} MotorCmdType;

/* ── 커맨드 프레임 (8바이트) ──────────────────────────────────────────── */
typedef struct __attribute__((packed)) {
    uint8_t  cmd_type;      /* MotorCmdType */
    uint8_t  motor_idx;     /* 0~3 */
    int16_t  target_rpm;    /* 목표 RPM (signed) */
    int8_t   duty_pct;      /* 듀티 -100~+100 (SET_DUTY 시 사용) */
    uint8_t  flags;         /* bit0=direction, bit1=brake_en, bit2=fault_clear */
    uint8_t  reserved[2];
} MotorCmdFrame;

/* ── 상태 프레임 (8바이트) ────────────────────────────────────────────── */
typedef struct __attribute__((packed)) {
    uint8_t  motor_idx;
    uint8_t  state;         /* 0=stopped, 1=running, 2=fault */
    int16_t  actual_rpm;
    int16_t  current_ma;    /* 전류 [mA] */
    uint8_t  fault_code;    /* 0=없음, 1=OCP, 2=OVP, 3=TMP */
    uint8_t  flags;
} MotorStatusFrame;

/* ── 수신 큐 ────────────────────────────────────────────────────────────── */
#define FDCAN_RX_QUEUE_SIZE 8U

typedef struct {
    MotorCmdFrame  buf[FDCAN_RX_QUEUE_SIZE];
    volatile uint8_t head;
    volatile uint8_t tail;
} MotorCmdQueue;

/* ── 모듈 핸들 ──────────────────────────────────────────────────────────── */
typedef struct {
    FDCAN_HandleTypeDef *hfdcan;
    MotorCmdQueue        rx_queue;
    uint8_t              motor_count;
    volatile bool        emergency_stop; /* 인터럽트에서 즉시 설정 */
} FDCANMotor_TypeDef;

void FDCAN_MotorCmd_Init(FDCANMotor_TypeDef *ctx, FDCAN_HandleTypeDef *hfdcan,
                         uint8_t motor_count);

/** TX: 상태 프레임 전송 */
HAL_StatusTypeDef FDCAN_MotorCmd_TxStatus(FDCANMotor_TypeDef *ctx,
                                           uint8_t motor_idx,
                                           int16_t rpm,
                                           int16_t current_ma,
                                           uint8_t state,
                                           uint8_t fault_code);

/** HAL_FDCAN_RxFifo0Callback 내부에서 호출 */
void FDCAN_MotorCmd_RxCallback(FDCANMotor_TypeDef *ctx,
                                FDCAN_HandleTypeDef *hfdcan);

/** 큐에서 커맨드 꺼내기 (메인 루프에서 호출) */
bool FDCAN_MotorCmd_Dequeue(FDCANMotor_TypeDef *ctx, MotorCmdFrame *out);
