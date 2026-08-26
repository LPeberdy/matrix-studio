#include "ms_client.h"

#include <cstdio>
#include <cstring>

#include "app_config.h"
#include "board_config.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "protocol/ms_protocol.h"
#include "protocol/reconnect_backoff.h"
#include "psram_info.h"
#include "wifi_station.h"

namespace matrix_studio {
namespace {

const char* TAG = "ms.proto";

namespace msp = matrix_studio_protocol;

constexpr EventBits_t kBitSocketUp = BIT0;
constexpr EventBits_t kBitSocketDown = BIT1;
constexpr EventBits_t kBitHelloAcked = BIT2;

// Smallest receive buffer that can hold one full frame for this panel.
constexpr size_t kMinRxCapacity = msp::kHeaderSizeBytes + msp::kFrameFixedFieldsLen + board::kFrameBytes;
// Largest message the protocol permits at all (docs/protocol.md §2).
constexpr size_t kMaxRxCapacity = msp::kHeaderSizeBytes + msp::kMaxPayloadBytes;

// How long to wait for the TCP+HTTP upgrade before giving up and backing off.
constexpr uint32_t kSocketConnectTimeoutMs = 15000;
// Service-loop tick. Fine enough to keep heartbeat and frame-timeout deadlines
// accurate to well within their tolerances, coarse enough to be free.
constexpr uint32_t kServiceTickMs = 100;

struct TxMessage {
  uint8_t data[kMaxEncodedTxBytes];
  size_t len;
};

int64_t now_us() { return esp_timer_get_time(); }
uint32_t elapsed_ms(int64_t since_us) { return static_cast<uint32_t>((now_us() - since_us) / 1000); }

class Client {
 public:
  esp_err_t start(FrameQueue* frames, QueueHandle_t commands) {
    frames_ = frames;
    commands_ = commands;

    events_ = xEventGroupCreate();
    tx_queue_ = xQueueCreate(8, sizeof(TxMessage));
    if (events_ == nullptr || tx_queue_ == nullptr) return ESP_ERR_NO_MEM;

    if (esp_err_t err = allocate_rx_buffer(); err != ESP_OK) return err;

    const BaseType_t ok = xTaskCreatePinnedToCore(&Client::task_entry, "ms_proto", 6144, this, 5, nullptr,
                                                  config::kNetworkCore);
    return ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
  }

  ClientStats stats() const {
    ClientStats s{};
    s.socket_connected = socket_connected_;
    s.handshaked = handshaked_;
    s.frames_received = frames_received_;
    s.frames_rejected = messages_rejected_;
    s.reconnects = reconnects_;
    s.last_sequence = last_sequence_;
    return s;
  }

 private:
  // -------------------------------------------------------------------------
  // Setup
  // -------------------------------------------------------------------------

  esp_err_t allocate_rx_buffer() {
    // Opportunistic PSRAM (docs/hardware.md): with PSRAM we can afford a buffer
    // big enough for any legal Protocol v1 message, so an oversized-but-legal
    // message from a future server is reassembled rather than dropped. Without
    // it, size for this panel's frame plus slack and say so in the log.
    if (psram::available()) {
      rx_capacity_ = kMaxRxCapacity;
      rx_ = static_cast<uint8_t*>(heap_caps_malloc(rx_capacity_, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
      if (rx_ != nullptr) {
        ESP_LOGI(TAG, "receive buffer: %u bytes in PSRAM (fits any legal v1 message)",
                 static_cast<unsigned>(rx_capacity_));
        return ESP_OK;
      }
      ESP_LOGW(TAG, "PSRAM receive-buffer allocation failed, falling back to internal SRAM");
    }

    rx_capacity_ = kMinRxCapacity + 512;
    rx_ = static_cast<uint8_t*>(heap_caps_malloc(rx_capacity_, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    if (rx_ == nullptr) {
      ESP_LOGE(TAG, "cannot allocate a %u-byte receive buffer", static_cast<unsigned>(rx_capacity_));
      return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "receive buffer: %u bytes in internal SRAM (messages larger than this are dropped)",
             static_cast<unsigned>(rx_capacity_));
    return ESP_OK;
  }

  static void task_entry(void* arg) { static_cast<Client*>(arg)->run(); }

  // -------------------------------------------------------------------------
  // Connection lifecycle (docs/protocol.md §3.3)
  // -------------------------------------------------------------------------

  [[noreturn]] void run() {
    char uri[160];
    std::snprintf(uri, sizeof(uri), "ws://%s:%d%s", config::kServerHost, config::kServerPort,
                  config::kServerPath);
    ESP_LOGI(TAG, "server %s", uri);
    ESP_LOGI(TAG, "device_id=%s fw=%s panel=%ux%u RGB565", wifi::device_id(), config::kFirmwareVersion,
             board::kPanelWidth, board::kPanelHeight);

    // Show the idle indicator straight away rather than leaving the panel dark
    // while Wi-Fi associates - a dark panel reads as a dead board.
    post_no_signal();

    for (;;) {
      if (!wifi::is_connected()) {
        post_state(ConnectionState::kWifiDown);
        ESP_LOGI(TAG, "waiting for Wi-Fi before connecting");
        // §3.3: re-establish Wi-Fi first, then apply backoff to the WebSocket.
        wifi::wait_connected(UINT32_MAX);
      }
      post_state(ConnectionState::kWifiUp);

      run_session(uri);

      // Any session end - clean close, error, failed heartbeat or Wi-Fi loss -
      // takes the same path. §3.4: a server restart is not a special case.
      post_no_signal();
      post_state(wifi::is_connected() ? ConnectionState::kWifiUp : ConnectionState::kWifiDown);
      ++reconnects_;
      const uint32_t delay_ms = backoff_.next_delay_ms();
      ESP_LOGW(TAG, "disconnected; reconnecting in %u ms", static_cast<unsigned>(delay_ms));
      vTaskDelay(pdMS_TO_TICKS(delay_ms));
    }
  }

  void run_session(const char* uri) {
    esp_websocket_client_config_t cfg = {};
    cfg.uri = uri;
    cfg.task_name = "ms_ws";
    cfg.task_stack = 5120;
    cfg.task_prio = 5;
    cfg.buffer_size = 4096;
    // §3.3 owns reconnection, with a specific backoff schedule. Letting the
    // library reconnect underneath us would give a second, conflicting policy.
    cfg.disable_auto_reconnect = true;
    cfg.network_timeout_ms = 10000;
    // The WebSocket-level ping is useful for NAT keepalive, but liveness is
    // decided by the Protocol v1 heartbeat in §3.1, so the library must not
    // drop the connection on its own timer.
    cfg.ping_interval_sec = 20;
    cfg.disable_pingpong_discon = true;

    xEventGroupClearBits(events_, kBitSocketUp | kBitSocketDown | kBitHelloAcked);
    reset_session_state();

    client_ = esp_websocket_client_init(&cfg);
    if (client_ == nullptr) {
      ESP_LOGE(TAG, "esp_websocket_client_init failed");
      return;
    }
    esp_websocket_register_events(client_, WEBSOCKET_EVENT_ANY, &Client::ws_event_trampoline, this);

    if (esp_websocket_client_start(client_) != ESP_OK) {
      ESP_LOGE(TAG, "esp_websocket_client_start failed");
      teardown();
      return;
    }

    ESP_LOGI(TAG, "connecting...");
    EventBits_t bits = xEventGroupWaitBits(events_, kBitSocketUp | kBitSocketDown, pdFALSE, pdFALSE,
                                           pdMS_TO_TICKS(kSocketConnectTimeoutMs));
    if ((bits & kBitSocketUp) == 0) {
      ESP_LOGW(TAG, "%s", (bits & kBitSocketDown) ? "connection refused" : "connection timed out");
      teardown();
      return;
    }

    post_state(ConnectionState::kSocketOpen);

    // §3.2: HELLO must be sent within HELLO_TIMEOUT_MS of the socket opening.
    // Sending it immediately is the only sane reading of that.
    if (!send_hello()) {
      teardown();
      return;
    }

    bits = xEventGroupWaitBits(events_, kBitHelloAcked | kBitSocketDown, pdFALSE, pdFALSE,
                               pdMS_TO_TICKS(msp::kHelloTimeoutMs));
    if ((bits & kBitHelloAcked) == 0) {
      ESP_LOGW(TAG, "no HELLO_ACK within %u ms, dropping connection",
               static_cast<unsigned>(msp::kHelloTimeoutMs));
      teardown();
      return;
    }

    // A handshake completed is what "the server is healthy" means, so this is
    // the only place the backoff resets (§3.3).
    backoff_.reset();
    handshaked_ = true;
    post_state(ConnectionState::kHandshaked);
    ESP_LOGI(TAG, "session established");

    service_session();
    teardown();
  }

  void service_session() {
    last_rx_us_ = now_us();
    last_frame_us_ = now_us();

    for (;;) {
      drain_tx_queue();

      const EventBits_t bits = xEventGroupGetBits(events_);
      if (bits & kBitSocketDown) {
        ESP_LOGW(TAG, "socket closed by peer or transport");
        return;
      }
      if (close_requested_) {
        ESP_LOGW(TAG, "closing connection: %s", close_reason_);
        // Give the outbound STATUS (if any) a moment to leave before the close,
        // since §3.5 asks for STATUS *then* close.
        drain_tx_queue();
        esp_websocket_client_close(client_, pdMS_TO_TICKS(1000));
        return;
      }
      if (!wifi::is_connected()) {
        ESP_LOGW(TAG, "Wi-Fi lost, abandoning session");
        return;
      }

      if (!check_heartbeat()) return;
      check_frame_timeout();

      vTaskDelay(pdMS_TO_TICKS(kServiceTickMs));
    }
  }

  // §3.1: answer every PING, and if our own PING goes unanswered for
  // PONG_TIMEOUT_MS treat the connection as dead. Returns false when dead.
  bool check_heartbeat() {
    if (ping_outstanding_) {
      if (elapsed_ms(ping_sent_us_) > msp::kPongTimeoutMs) {
        ESP_LOGW(TAG, "no PONG for nonce 0x%08x within %u ms - connection is dead",
                 static_cast<unsigned>(ping_nonce_), static_cast<unsigned>(msp::kPongTimeoutMs));
        return false;
      }
      return true;
    }

    // Anything received counts as evidence of liveness (§3.1: "an in-flight
    // frame stream is itself evidence of liveness"), so only ping when idle.
    if (elapsed_ms(last_rx_us_) >= msp::kPingIntervalMs) {
      ping_nonce_ = esp_random();
      uint8_t buf[kMaxEncodedTxBytes];
      const size_t n = encode_ping(buf, sizeof(buf), ping_nonce_);
      if (n > 0 && enqueue_tx(buf, n)) {
        ping_outstanding_ = true;
        ping_sent_us_ = now_us();
        ESP_LOGD(TAG, "PING 0x%08x", static_cast<unsigned>(ping_nonce_));
      }
    }
    return true;
  }

  // §3.2: no FRAME for FRAME_TIMEOUT_MS means show a quiet fallback, but
  // explicitly does NOT mean drop the connection.
  void check_frame_timeout() {
    if (no_signal_) return;
    if (elapsed_ms(last_frame_us_) < config::kFrameTimeoutMs) return;
    no_signal_ = true;
    ESP_LOGI(TAG, "no FRAME for %u ms - entering no-signal state (connection stays open)",
             static_cast<unsigned>(config::kFrameTimeoutMs));
    post_no_signal();
    post_state(ConnectionState::kHandshaked);
  }

  void teardown() {
    socket_connected_ = false;
    handshaked_ = false;
    if (client_ != nullptr) {
      esp_websocket_client_stop(client_);
      esp_websocket_client_destroy(client_);
      client_ = nullptr;
    }
    // Anything queued belonged to the session that just ended.
    xQueueReset(tx_queue_);
  }

  void reset_session_state() {
    close_requested_ = false;
    close_reason_ = "";
    handshaked_ = false;
    ping_outstanding_ = false;
    no_signal_ = false;
    rx_len_ = 0;
    rx_overflow_ = false;
  }

  // -------------------------------------------------------------------------
  // Outbound
  // -------------------------------------------------------------------------

  // Every send happens on this task, never from the WebSocket event handler:
  // sending from inside a handler re-enters the client's own lock.
  bool enqueue_tx(const uint8_t* data, size_t len) {
    if (len > kMaxEncodedTxBytes) return false;
    TxMessage msg;
    std::memcpy(msg.data, data, len);
    msg.len = len;
    if (xQueueSend(tx_queue_, &msg, 0) != pdTRUE) {
      ESP_LOGW(TAG, "transmit queue full, dropping an outbound message");
      return false;
    }
    return true;
  }

  void drain_tx_queue() {
    TxMessage msg;
    while (xQueueReceive(tx_queue_, &msg, 0) == pdTRUE) {
      if (client_ == nullptr) return;
      const int sent = esp_websocket_client_send_bin(client_, reinterpret_cast<const char*>(msg.data),
                                                     static_cast<int>(msg.len), pdMS_TO_TICKS(2000));
      if (sent < 0) {
        ESP_LOGW(TAG, "send failed (%d bytes)", static_cast<int>(msg.len));
        request_close("send failed");
        return;
      }
    }
  }

  bool send_hello() {
    uint8_t buf[kMaxEncodedTxBytes];
    const size_t n = encode_hello(buf, sizeof(buf), board::kPanelWidth, board::kPanelHeight,
                                  static_cast<uint8_t>(msp::PixelFormat::kRgb565), wifi::device_id(),
                                  config::kFirmwareVersion);
    if (n == 0) {
      ESP_LOGE(TAG, "failed to encode HELLO (device_id or fw_version too long?)");
      return false;
    }
    const int sent = esp_websocket_client_send_bin(client_, reinterpret_cast<const char*>(buf),
                                                   static_cast<int>(n), pdMS_TO_TICKS(2000));
    if (sent < 0) {
      ESP_LOGW(TAG, "failed to send HELLO");
      return false;
    }
    ESP_LOGI(TAG, "sent HELLO (device_id=%s, %ux%u RGB565)", wifi::device_id(), board::kPanelWidth,
             board::kPanelHeight);
    return true;
  }

  void send_status(msp::StatusCode code, const char* message) {
    uint8_t buf[kMaxEncodedTxBytes];
    const size_t n = encode_status(buf, sizeof(buf), code, message);
    if (n > 0) enqueue_tx(buf, n);
  }

  void request_close(const char* reason) {
    if (close_requested_) return;
    close_requested_ = true;
    close_reason_ = reason;
  }

  // -------------------------------------------------------------------------
  // Inbound
  // -------------------------------------------------------------------------

  static void ws_event_trampoline(void* arg, esp_event_base_t base, int32_t id, void* data) {
    static_cast<Client*>(arg)->on_ws_event(base, id, static_cast<esp_websocket_event_data_t*>(data));
  }

  void on_ws_event(esp_event_base_t, int32_t id, esp_websocket_event_data_t* data) {
    switch (id) {
      case WEBSOCKET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "WebSocket connected to %s:%d%s", config::kServerHost, config::kServerPort,
                 config::kServerPath);
        socket_connected_ = true;
        xEventGroupSetBits(events_, kBitSocketUp);
        break;

      case WEBSOCKET_EVENT_DISCONNECTED:
      case WEBSOCKET_EVENT_CLOSED:
        socket_connected_ = false;
        xEventGroupSetBits(events_, kBitSocketDown);
        break;

      case WEBSOCKET_EVENT_ERROR:
        if (data != nullptr) {
          ESP_LOGW(TAG, "WebSocket error (type %d, http status %d, sock errno %d)",
                   static_cast<int>(data->error_handle.error_type),
                   data->error_handle.esp_ws_handshake_status_code,
                   data->error_handle.esp_transport_sock_errno);
        }
        socket_connected_ = false;
        xEventGroupSetBits(events_, kBitSocketDown);
        break;

      case WEBSOCKET_EVENT_DATA:
        if (data != nullptr) on_ws_data(data);
        break;

      default:
        break;
    }
  }

  // Reassembles one protocol message from however many DATA events the client
  // splits it across (an 8214-byte FRAME does not fit one 4KB read).
  void on_ws_data(const esp_websocket_event_data_t* d) {
    last_rx_us_ = now_us();

    const uint8_t op = static_cast<uint8_t>(d->op_code) & 0x0F;
    if (op == WS_TRANSPORT_OPCODES_CLOSE) {
      ESP_LOGI(TAG, "server sent a WebSocket close frame");
      xEventGroupSetBits(events_, kBitSocketDown);
      return;
    }
    if (op == WS_TRANSPORT_OPCODES_PING || op == WS_TRANSPORT_OPCODES_PONG) return;  // transport-level
    if (op == WS_TRANSPORT_OPCODES_TEXT) {
      ESP_LOGW(TAG, "ignoring a text WebSocket message; Protocol v1 is binary only");
      return;
    }
    if (op != WS_TRANSPORT_OPCODES_BINARY && op != WS_TRANSPORT_OPCODES_CONT) return;

    if (d->data_len < 0 || d->payload_len < 0 || d->payload_offset < 0) return;
    const size_t total = static_cast<size_t>(d->payload_len);
    const size_t offset = static_cast<size_t>(d->payload_offset);
    const size_t chunk = static_cast<size_t>(d->data_len);

    if (offset == 0) {
      rx_len_ = 0;
      rx_overflow_ = false;
    }

    if (total > rx_capacity_) {
      if (!rx_overflow_) {
        rx_overflow_ = true;
        if (total > kMaxRxCapacity) {
          // §3.5(4): a declared length above MAX_PAYLOAD_BYTES is fatal, and
          // must not be used to size a read.
          ESP_LOGE(TAG, "message of %u bytes exceeds the protocol maximum", static_cast<unsigned>(total));
          request_close("declared length exceeds MAX_PAYLOAD_BYTES");
        } else {
          ESP_LOGW(TAG, "message of %u bytes exceeds the %u-byte receive buffer, discarding it",
                   static_cast<unsigned>(total), static_cast<unsigned>(rx_capacity_));
          ++messages_rejected_;
        }
      }
      return;  // nothing is copied, so nothing can overrun
    }
    if (rx_overflow_) return;

    // Defensive: the library should never hand us a chunk that runs past the
    // declared total, but this buffer is the one thing between the network and
    // an out-of-bounds write.
    if (offset > rx_capacity_ || chunk > rx_capacity_ - offset) {
      ESP_LOGE(TAG, "dropping a chunk that would overrun the receive buffer (offset %u, len %u, cap %u)",
               static_cast<unsigned>(offset), static_cast<unsigned>(chunk),
               static_cast<unsigned>(rx_capacity_));
      rx_overflow_ = true;
      ++messages_rejected_;
      return;
    }

    if (chunk > 0) std::memcpy(rx_ + offset, d->data_ptr, chunk);
    rx_len_ = offset + chunk;

    if (rx_len_ >= total) {
      handle_message(rx_, rx_len_);
      rx_len_ = 0;
    }
  }

  void handle_message(const uint8_t* buf, size_t len) {
    Message m;
    const ParseResult result = parse_message(buf, len, m);

    if (result != ParseResult::kOk) {
      ++messages_rejected_;
      ESP_LOGW(TAG, "rejected message: %s (type=0x%02x, declared length=%u, received %u bytes)",
               parse_result_name(result), m.header.type, static_cast<unsigned>(m.header.length),
               static_cast<unsigned>(len));

      msp::StatusCode code;
      if (status_code_for(result, &code)) send_status(code, parse_result_name(result));
      // §3.5: framing corruption is fatal; a well-framed bad message is not.
      if (is_fatal(result)) request_close(parse_result_name(result));
      return;
    }

    if (has_reserved_flags(m)) {
      ESP_LOGW(TAG, "message type 0x%02x has reserved header flags 0x%02x set; ignoring them", m.header.type,
               m.header.flags);
    }

    switch (m.type) {
      case msp::MessageType::kHelloAck:
        ESP_LOGI(TAG, "HELLO_ACK: version %u, frame interval hint %u ms, server time %u",
                 m.hello_ack.protocol_version, m.hello_ack.frame_interval_hint_ms,
                 static_cast<unsigned>(m.hello_ack.server_time_unix));
        xEventGroupSetBits(events_, kBitHelloAcked);
        break;

      case msp::MessageType::kFrame:
        on_frame(m);
        break;

      case msp::MessageType::kBrightness: {
        ESP_LOGI(TAG, "BRIGHTNESS %u", m.brightness);
        DisplayCommand cmd{};
        cmd.kind = DisplayCommand::Kind::kBrightness;
        cmd.brightness = m.brightness;
        post_command(cmd);
        break;
      }

      case msp::MessageType::kBlank: {
        ESP_LOGI(TAG, "BLANK %s", m.blank ? "on" : "off");
        DisplayCommand cmd{};
        cmd.kind = DisplayCommand::Kind::kBlank;
        cmd.blank = m.blank;
        post_command(cmd);
        break;
      }

      case msp::MessageType::kPing: {
        uint8_t buf_out[kMaxEncodedTxBytes];
        const size_t n = encode_pong(buf_out, sizeof(buf_out), m.nonce);
        if (n > 0) enqueue_tx(buf_out, n);
        ESP_LOGD(TAG, "PING 0x%08x -> PONG", static_cast<unsigned>(m.nonce));
        break;
      }

      case msp::MessageType::kPong:
        if (ping_outstanding_ && m.nonce == ping_nonce_) {
          ping_outstanding_ = false;
          ESP_LOGD(TAG, "PONG 0x%08x", static_cast<unsigned>(m.nonce));
        } else {
          ESP_LOGW(TAG, "unexpected PONG nonce 0x%08x", static_cast<unsigned>(m.nonce));
        }
        break;

      case msp::MessageType::kStatus:
        ESP_LOGW(TAG, "STATUS from server: code %u \"%.*s\"", m.status.code,
                 static_cast<int>(m.status.text_len), m.status.text ? m.status.text : "");
        if (m.status.code == static_cast<uint16_t>(msp::StatusCode::kErrUnsupportedVersion)) {
          request_close("server rejected our protocol version");
        }
        break;

      case msp::MessageType::kHello:
        // Well-formed but server->device is not a direction HELLO travels.
        ESP_LOGW(TAG, "ignoring a HELLO from the server (wrong direction for this message type)");
        break;
    }
  }

  void on_frame(const Message& m) {
    // §3.5(5)/§4.3: dimensions must match what we advertised in HELLO.
    if (m.frame.width != board::kPanelWidth || m.frame.height != board::kPanelHeight) {
      ++messages_rejected_;
      ESP_LOGW(TAG, "FRAME is %ux%u but this panel is %ux%u; discarding", m.frame.width, m.frame.height,
               board::kPanelWidth, board::kPanelHeight);
      send_status(msp::StatusCode::kErrDimensionMismatch, "frame dimensions do not match HELLO");
      return;
    }

    uint8_t* slot = frames_->begin_write();
    if (slot == nullptr) {
      ++messages_rejected_;
      return;
    }
    std::memcpy(slot, m.frame.pixels, m.frame.pixel_bytes);
    frames_->commit_write(m.frame.sequence);

    last_frame_us_ = now_us();
    last_sequence_ = m.frame.sequence;
    ++frames_received_;

    if (no_signal_) {
      no_signal_ = false;
      ESP_LOGI(TAG, "frames resumed at sequence %u", static_cast<unsigned>(m.frame.sequence));
    }
    if (!streaming_) {
      streaming_ = true;
      post_state(ConnectionState::kStreaming);
    }
    if ((frames_received_ % 300u) == 1u) {
      ESP_LOGI(TAG, "%u frames received (seq %u, %u dropped by the render queue)",
               static_cast<unsigned>(frames_received_), static_cast<unsigned>(m.frame.sequence),
               static_cast<unsigned>(frames_->dropped_frames()));
    }
  }

  // -------------------------------------------------------------------------
  // Render-task commands
  // -------------------------------------------------------------------------

  void post_command(const DisplayCommand& cmd) {
    if (commands_ == nullptr) return;
    if (xQueueSend(commands_, &cmd, 0) != pdTRUE) ESP_LOGW(TAG, "display command queue full");
  }

  void post_state(ConnectionState state) {
    if (state != ConnectionState::kStreaming) streaming_ = false;
    DisplayCommand cmd{};
    cmd.kind = DisplayCommand::Kind::kConnectionState;
    cmd.state = state;
    post_command(cmd);
  }

  void post_no_signal() {
    DisplayCommand cmd{};
    cmd.kind = DisplayCommand::Kind::kNoSignal;
    post_command(cmd);
  }

  // -------------------------------------------------------------------------

  FrameQueue* frames_ = nullptr;
  QueueHandle_t commands_ = nullptr;
  EventGroupHandle_t events_ = nullptr;
  QueueHandle_t tx_queue_ = nullptr;
  esp_websocket_client_handle_t client_ = nullptr;

  uint8_t* rx_ = nullptr;
  size_t rx_capacity_ = 0;
  size_t rx_len_ = 0;
  bool rx_overflow_ = false;

  ReconnectBackoff backoff_;

  volatile bool socket_connected_ = false;
  volatile bool handshaked_ = false;
  volatile bool close_requested_ = false;
  const char* close_reason_ = "";
  bool streaming_ = false;
  bool no_signal_ = false;

  bool ping_outstanding_ = false;
  uint32_t ping_nonce_ = 0;
  int64_t ping_sent_us_ = 0;
  int64_t last_rx_us_ = 0;
  int64_t last_frame_us_ = 0;

  uint32_t frames_received_ = 0;
  uint32_t messages_rejected_ = 0;
  uint32_t reconnects_ = 0;
  uint32_t last_sequence_ = 0;
};

Client g_client;

}  // namespace

esp_err_t ms_client_start(FrameQueue* frames, QueueHandle_t commands) {
  return g_client.start(frames, commands);
}

ClientStats ms_client_stats() { return g_client.stats(); }

}  // namespace matrix_studio
