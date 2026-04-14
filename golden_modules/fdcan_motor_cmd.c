/**
 * fdcan_motor_cmd.c — FDCAN 커맨드 파싱 (모터 제어 명령 수신/송신)
 * Golden Module: MotorDriveForge
 */
#include "fdcan_motor_cmd.h"
#include <string.h>

/* ── 내부 큐 헬퍼 ──────────────────────────────────────────────────────── */

static bool queue_enqueue(MotorCmdQueue *q, const MotorCmdFrame *frame)
{
    uint8_t next = (q->head + 1) % FDCAN_RX_QUEUE_SIZE;
    if (next == q->tail) return false; /* 큐 가득 */
    q->buf[q->head] = *frame;
    q->head = next;
    return true;
}

static bool queue_dequeue(MotorCmdQueue *q, MotorCmdFrame *out)
{
    if (q->head == q->tail) return false; /* 빈 큐 */
    *out = q->buf[q->tail];
    q->tail = (q->tail + 1) % FDCAN_RX_QUEUE_SIZE;
    return true;
}

/* ── 공개 API ───────────────────────────────────────────────────────────── */

void FDCAN_MotorCmd_Init(FDCANMotor_TypeDef *ctx, FDCAN_HandleTypeDef *hfdcan,
                         uint8_t motor_count)
{
    ctx->hfdcan         = hfdcan;
    ctx->motor_count    = (motor_count > FDCAN_MAX_MOTORS) ? FDCAN_MAX_MOTORS : motor_count;
    ctx->emergency_stop = false;
    memset(&ctx->rx_queue, 0, sizeof(ctx->rx_queue));

    /* 수신 필터 설정: 0x100~0x100+motor_count-1 */
    FDCAN_FilterTypeDef filter = {
        .IdType       = FDCAN_STANDARD_ID,
        .FilterIndex  = 0,
        .FilterType   = FDCAN_FILTER_RANGE,
        .FilterConfig = FDCAN_FILTER_TO_RXFIFO0,
        .FilterID1    = FDCAN_CMD_BASE_ID,
        .FilterID2    = FDCAN_CMD_BASE_ID + ctx->motor_count - 1,
    };
    HAL_FDCAN_ConfigFilter(hfdcan, &filter);

    /* 비상 정지 필터 (0x1FF or broadcast) */
    FDCAN_FilterTypeDef emg_filter = {
        .IdType       = FDCAN_STANDARD_ID,
        .FilterIndex  = 1,
        .FilterType   = FDCAN_FILTER_DUAL,
        .FilterConfig = FDCAN_FILTER_TO_RXFIFO0,
        .FilterID1    = 0x1FF,
        .FilterID2    = 0x1FF,
    };
    HAL_FDCAN_ConfigFilter(hfdcan, &emg_filter);

    HAL_FDCAN_ActivateNotification(hfdcan, FDCAN_IT_RX_FIFO0_NEW_MESSAGE, 0);
    HAL_FDCAN_Start(hfdcan);
}

HAL_StatusTypeDef FDCAN_MotorCmd_TxStatus(FDCANMotor_TypeDef *ctx,
                                           uint8_t motor_idx,
                                           int16_t rpm,
                                           int16_t current_ma,
                                           uint8_t state,
                                           uint8_t fault_code)
{
    if (motor_idx >= ctx->motor_count) return HAL_ERROR;

    MotorStatusFrame sf = {
        .motor_idx  = motor_idx,
        .state      = state,
        .actual_rpm = rpm,
        .current_ma = current_ma,
        .fault_code = fault_code,
        .flags      = ctx->emergency_stop ? 0x80U : 0x00U,
    };

    FDCAN_TxHeaderTypeDef tx_hdr = {
        .Identifier          = FDCAN_STATUS_BASE_ID + motor_idx,
        .IdType              = FDCAN_STANDARD_ID,
        .TxFrameType         = FDCAN_DATA_FRAME,
        .DataLength          = FDCAN_DLC_BYTES_8,
        .ErrorStateIndicator = FDCAN_ESI_ACTIVE,
        .BitRateSwitch       = FDCAN_BRS_OFF,
        .FDFormat            = FDCAN_CLASSIC_CAN,
        .TxEventFifoControl  = FDCAN_NO_TX_EVENTS,
        .MessageMarker       = 0,
    };

    return HAL_FDCAN_AddMessageToTxFifoQ(ctx->hfdcan, &tx_hdr, (uint8_t *)&sf);
}

void FDCAN_MotorCmd_RxCallback(FDCANMotor_TypeDef *ctx,
                                FDCAN_HandleTypeDef *hfdcan)
{
    FDCAN_RxHeaderTypeDef rx_hdr;
    uint8_t rx_data[8];

    while (HAL_FDCAN_GetRxMessage(hfdcan, FDCAN_RX_FIFO0, &rx_hdr, rx_data) == HAL_OK) {
        MotorCmdFrame frame;
        memcpy(&frame, rx_data, sizeof(frame));

        /* 비상 정지: 즉시 처리 (인터럽트 컨텍스트) */
        if (frame.cmd_type == MOTOR_CMD_EMERGENCY_STOP || rx_hdr.Identifier == 0x1FF) {
            ctx->emergency_stop = true;
            /* 큐에도 넣어서 메인 루프에서 상태 업데이트 가능하도록 */
            queue_enqueue(&ctx->rx_queue, &frame);
            continue;
        }

        /* 일반 커맨드: 큐에 적재 */
        queue_enqueue(&ctx->rx_queue, &frame);
    }

    /* 다음 메시지 알림 재등록 */
    HAL_FDCAN_ActivateNotification(hfdcan, FDCAN_IT_RX_FIFO0_NEW_MESSAGE, 0);
}

bool FDCAN_MotorCmd_Dequeue(FDCANMotor_TypeDef *ctx, MotorCmdFrame *out)
{
    return queue_dequeue(&ctx->rx_queue, out);
}
