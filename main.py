"""
Racing Pedals - Kivy app.

Features:
- Accelerator and brake work independently.
- Multi-touch support.
- HOLD mode: pedal returns to 0% when finger is released.
- LATCH mode: pedal stays at the last position after finger release.
- RESET button returns both pedals to 0%.
- Values stream to Windows PC over USB using adb forward.

Wire protocol:
    A:0.80;B:0.30\n
"""

import socket
import threading
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.widget import Widget


PORT = 6600
SEND_INTERVAL = 1 / 60.0


# --------------------------------------------------------------------------
# Shared pedal state
# --------------------------------------------------------------------------
class PedalState:

    def __init__(self):
        self.accelerator = 0.0
        self.brake = 0.0
        self._lock = threading.Lock()

    def set_accelerator(self, value):
        with self._lock:
            self.accelerator = max(0.0, min(1.0, value))

    def set_brake(self, value):
        with self._lock:
            self.brake = max(0.0, min(1.0, value))

    def reset(self):
        with self._lock:
            self.accelerator = 0.0
            self.brake = 0.0

    def to_wire_format(self):
        with self._lock:
            return f"A:{self.accelerator:.2f};B:{self.brake:.2f}\n"


# --------------------------------------------------------------------------
# TCP bridge
# --------------------------------------------------------------------------
class TcpBridge:

    def __init__(self, pedal_state, status_callback):
        self.pedal_state = pedal_state
        self.status_callback = status_callback

        self._server_socket = None
        self._client_socket = None
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return

        self._running = True

        self._thread = threading.Thread(
            target=self._run,
            daemon=True
        )

        self._thread.start()

    def stop(self):
        self._running = False

        try:
            if self._client_socket:
                self._client_socket.close()
        except OSError:
            pass

        try:
            if self._server_socket:
                self._server_socket.close()
        except OSError:
            pass

        self._client_socket = None
        self._server_socket = None

        self.status_callback("STOPPED")

    def _run(self):

        try:
            self._server_socket = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM
            )

            self._server_socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_REUSEADDR,
                1
            )

            self._server_socket.bind(
                ("127.0.0.1", PORT)
            )

            self._server_socket.listen(1)
            self._server_socket.settimeout(1.0)

            self.status_callback(
                "WAITING FOR PC (run: adb forward)"
            )

        except OSError as e:

            self.status_callback(f"ERROR: {e}")
            self._running = False
            return

        while self._running:

            try:
                client, _addr = self._server_socket.accept()

            except socket.timeout:
                continue

            except OSError:
                break

            self._client_socket = client

            self.status_callback("CONNECTED")

            self._stream_to_client(client)

            self._client_socket = None

            if self._running:
                self.status_callback(
                    "WAITING FOR PC (run: adb forward)"
                )

    def _stream_to_client(self, client):

        client.settimeout(2.0)

        while self._running:

            try:

                packet = (
                    self.pedal_state
                    .to_wire_format()
                    .encode("ascii")
                )

                client.sendall(packet)

                time.sleep(SEND_INTERVAL)

            except (OSError, BrokenPipeError):
                break

        try:
            client.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Pedal bar
# --------------------------------------------------------------------------
class PedalBar(Widget):

    def __init__(
        self,
        label_text,
        color,
        on_change,
        on_release,
        latch_mode=False,
        **kwargs
    ):

        super().__init__(**kwargs)

        self.value = 0.0
        self.color = color

        self.on_change = on_change
        self.on_release = on_release

        # False = HOLD mode
        # True  = LATCH mode
        self.latch_mode = latch_mode

        self._touches = set()

        with self.canvas:

            Color(1, 1, 1, 0.08)

            self.bg_rect = Rectangle(
                pos=self.pos,
                size=self.size
            )

            Color(*color)

            self.fill_rect = Rectangle(
                pos=self.pos,
                size=(self.size[0], 0)
            )

        self.bind(
            pos=self._redraw,
            size=self._redraw
        )

    def _redraw(self, *_args):

        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size

        self.fill_rect.pos = self.pos

        self.fill_rect.size = (
            self.size[0],
            self.size[1] * self.value
        )

    def _value_from_touch(self, touch):

        local_y = touch.y - self.y

        fraction = (
            local_y / self.height
            if self.height
            else 0.0
        )

        return max(
            0.0,
            min(1.0, fraction)
        )

    def on_touch_down(self, touch):

        if not self.collide_point(*touch.pos):
            return False

        self._touches.add(touch.uid)

        touch.grab(self)

        self.value = self._value_from_touch(touch)

        self._redraw()

        self.on_change(self.value)

        return True

    def on_touch_move(self, touch):

        if touch.grab_current is not self:
            return False

        self.value = self._value_from_touch(touch)

        self._redraw()

        self.on_change(self.value)

        return True

    def on_touch_up(self, touch):

        if touch.grab_current is not self:
            return False

        touch.ungrab(self)

        self._touches.discard(touch.uid)

        # Only reset when ALL fingers have left this pedal.
        if not self._touches:

            # HOLD MODE
            if not self.latch_mode:

                self.value = 0.0

                self._redraw()

                self.on_release()

            # LATCH MODE
            # Do nothing.
            # The value remains where the user left it.

        return True

    def reset(self):

        self._touches.clear()

        self.value = 0.0

        self._redraw()

        self.on_release()


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------
class RacingPedalsApp(App):

    def build(self):

        self.pedal_state = PedalState()

        self.bridge = TcpBridge(
            self.pedal_state,
            self._on_bridge_status
        )

        self._bridge_running = False

        root = BoxLayout(
            orientation="vertical"
        )

        # --------------------------------------------------------------
        # Header
        # --------------------------------------------------------------

        header = BoxLayout(
            size_hint=(1, 0.12),
            padding=10
        )

        header.add_widget(
            Label(
                text="RACING PEDALS",
                bold=True,
                font_size="20sp"
            )
        )

        self.status_label = Label(
            text="STOPPED",
            color=(0.7, 0.7, 0.7, 1)
        )

        header.add_widget(self.status_label)

        root.add_widget(header)

        # --------------------------------------------------------------
        # Pedal bars
        # --------------------------------------------------------------

        bars_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, 0.65),
            spacing=16,
            padding=16
        )

        # ==============================================================
        # ACCELERATOR
        # ==============================================================

        accel_col = BoxLayout(
            orientation="vertical"
        )

        accel_col.add_widget(
            Label(
                text="ACCELERATOR",
                bold=True,
                size_hint=(1, 0.08)
            )
        )

        self.accel_bar = PedalBar(
            "ACCELERATOR",
            (0.2, 0.8, 0.3, 1),
            on_change=self._on_accel_change,
            on_release=self._on_accel_release
        )

        accel_col.add_widget(self.accel_bar)

        self.accel_label = Label(
            text="0%",
            size_hint=(1, 0.08),
            font_size="18sp"
        )

        accel_col.add_widget(self.accel_label)

        # Accelerator mode button

        self.accel_mode_button = Button(
            text="MODE: HOLD",
            size_hint=(1, 0.10)
        )

        self.accel_mode_button.bind(
            on_press=self._toggle_accel_mode
        )

        accel_col.add_widget(
            self.accel_mode_button
        )

        bars_row.add_widget(accel_col)

        # ==============================================================
        # BRAKE
        # ==============================================================

        brake_col = BoxLayout(
            orientation="vertical"
        )

        brake_col.add_widget(
            Label(
                text="BRAKE",
                bold=True,
                size_hint=(1, 0.08)
            )
        )

        self.brake_bar = PedalBar(
            "BRAKE",
            (0.9, 0.25, 0.2, 1),
            on_change=self._on_brake_change,
            on_release=self._on_brake_release
        )

        brake_col.add_widget(self.brake_bar)

        self.brake_label = Label(
            text="0%",
            size_hint=(1, 0.08),
            font_size="18sp"
        )

        brake_col.add_widget(self.brake_label)

        # Brake mode button

        self.brake_mode_button = Button(
            text="MODE: HOLD",
            size_hint=(1, 0.10)
        )

        self.brake_mode_button.bind(
            on_press=self._toggle_brake_mode
        )

        brake_col.add_widget(
            self.brake_mode_button
        )

        bars_row.add_widget(brake_col)

        root.add_widget(bars_row)

        # --------------------------------------------------------------
        # Bottom buttons
        # --------------------------------------------------------------

        controls = BoxLayout(
            orientation="horizontal",
            size_hint=(1, 0.23),
            spacing=10,
            padding=10
        )

        # RESET

        self.reset_button = Button(
            text="RESET PEDALS"
        )

        self.reset_button.bind(
            on_press=self._reset_pedals
        )

        controls.add_widget(
            self.reset_button
        )

        # CONNECTION

        self.toggle_button = Button(
            text="START CONNECTION"
        )

        self.toggle_button.bind(
            on_press=self._toggle_connection
        )

        controls.add_widget(
            self.toggle_button
        )

        root.add_widget(controls)

        Clock.schedule_interval(
            self._refresh_labels,
            1 / 30.0
        )

        return root

    # ------------------------------------------------------------------
    # Accelerator
    # ------------------------------------------------------------------

    def _on_accel_change(self, value):

        self.pedal_state.set_accelerator(value)

    def _on_accel_release(self):

        self.pedal_state.set_accelerator(0.0)

    def _toggle_accel_mode(self, _instance):

        self.accel_bar.latch_mode = (
            not self.accel_bar.latch_mode
        )

        if self.accel_bar.latch_mode:

            self.accel_mode_button.text = "MODE: LATCH"

        else:

            self.accel_mode_button.text = "MODE: HOLD"

            # If switching back to HOLD while not touching,
            # immediately release the pedal.
            if not self.accel_bar._touches:

                self.accel_bar.reset()

    # ------------------------------------------------------------------
    # Brake
    # ------------------------------------------------------------------

    def _on_brake_change(self, value):

        self.pedal_state.set_brake(value)

    def _on_brake_release(self):

        self.pedal_state.set_brake(0.0)

    def _toggle_brake_mode(self, _instance):

        self.brake_bar.latch_mode = (
            not self.brake_bar.latch_mode
        )

        if self.brake_bar.latch_mode:

            self.brake_mode_button.text = "MODE: LATCH"

        else:

            self.brake_mode_button.text = "MODE: HOLD"

            if not self.brake_bar._touches:

                self.brake_bar.reset()

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def _reset_pedals(self, _instance):

        self.accel_bar.reset()
        self.brake_bar.reset()

        self.pedal_state.reset()

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def _refresh_labels(self, _dt):

        self.accel_label.text = (
            f"{round(self.pedal_state.accelerator * 100)}%"
        )

        self.brake_label.text = (
            f"{round(self.pedal_state.brake * 100)}%"
        )

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _toggle_connection(self, _instance):

        if self._bridge_running:

            self.bridge.stop()

            self._bridge_running = False

            self.toggle_button.text = (
                "START CONNECTION"
            )

        else:

            self.bridge.start()

            self._bridge_running = True

            self.toggle_button.text = (
                "STOP CONNECTION"
            )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _on_bridge_status(self, status_text):

        Clock.schedule_once(
            lambda _dt: setattr(
                self.status_label,
                "text",
                status_text
            ),
            0
        )

    # ------------------------------------------------------------------
    # App shutdown
    # ------------------------------------------------------------------

    def on_stop(self):

        self.bridge.stop()


if __name__ == "__main__":
    RacingPedalsApp().run()
