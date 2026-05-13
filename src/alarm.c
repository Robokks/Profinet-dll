#include "alarm.h"
#include "frame_io.h"
#include "utils.h"
#include <string.h>

/* ─── RTA frame layout (after Ethernet header + FrameID) ────────────────── */
/* RTA_SDU: PDUType(2) AddFlags(2) SendSeqNum(2) AckSeqNum(2)
 *           VarPartLen(2) VarPart... APDU_Status(4) */
#define RTA_OFF_PDUTYPE     0
#define RTA_OFF_ADDFLAGS    2
#define RTA_OFF_SENDSEQ     4
#define RTA_OFF_ACKSEQ      6
#define RTA_OFF_VARPARTLEN  8
#define RTA_OFF_VARPART     10
#define RTA_HDR_MIN         10

void alarm_handle_frame(PN_Context *ctx, const uint8_t *frame, int len)
{
    if (len < ETH_HDR_LEN + 2 + RTA_HDR_MIN) return;

    uint16_t fid = pn_get_u16be(frame, ETH_HDR_LEN);
    if (fid != ALARM_FRAMEID_HIGH && fid != ALARM_FRAMEID_LOW) return;

    const uint8_t *rta = frame + ETH_HDR_LEN + 2;
    int rta_len = len - ETH_HDR_LEN - 2;
    if (rta_len < RTA_HDR_MIN) return;

    uint16_t pdu_type  = pn_get_u16be(rta, RTA_OFF_PDUTYPE) & 0x0FFFu;
    uint16_t send_seq  = pn_get_u16be(rta, RTA_OFF_SENDSEQ);

    if (pdu_type != RTA_PDU_TYPE_DATA) return;

    pn_log("Alarm received: FrameID=0x%04X SendSeq=%u", fid, send_seq);

    /* Build ACK frame */
    uint8_t ack[ETH_MAX_FRAME];
    int off = eth_build_header(ack, frame + 6 /* dst = sender src */,
                                ctx->local_mac, ETHERTYPE_PROFINET);
    /* FrameID same as alarm */
    pn_put_u16be(ack, off, fid); off += 2;
    /* RTA ACK PDU */
    pn_put_u16be(ack, off, RTA_PDU_TYPE_ACK); off += 2; /* PDUType=ACK */
    pn_put_u16be(ack, off, 0x0000);           off += 2; /* AddFlags */
    pn_put_u16be(ack, off, 0x0000);           off += 2; /* SendSeqNum (not used in ACK) */
    pn_put_u16be(ack, off, send_seq);         off += 2; /* AckSeqNum = alarm's SendSeqNum */
    pn_put_u16be(ack, off, 0x0000);           off += 2; /* VarPartLen = 0 */
    /* APDU Status */
    pn_put_u32be(ack, off, 0x00000000);       off += 4;

    if (off < ETH_MIN_FRAME) {
        memset(ack + off, 0, (size_t)(ETH_MIN_FRAME - off));
        off = ETH_MIN_FRAME;
    }

    frameio_send((FrameIO *)ctx->pcap, ack, off);
    pn_log("Alarm ACK sent");
}
