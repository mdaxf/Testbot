from __future__ import annotations

import base64
import json
import queue
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from framework.models import TestStep
from framework.variables.context import VariableContext

INTEGRATION_ACTIONS = ("rest_call", "bus_subscribe", "bus_wait", "bus_publish")


class StepAssertionFailed(Exception):
    """An expectation inside an integration step's `config` did not hold -- reported as a
    failed step (not an errored one), same as a failed `expected` block on a UI step."""


# --------------------------------------------------------------------------- helpers

_PATH_TOKEN_RE = re.compile(r"[^.\[\]]+|\[\d+\]")


def get_path(obj: Any, path: str) -> Any:
    """Tiny JSONPath subset: 'data.items[0].id', optionally prefixed with '$' / '$.'.
    Raises KeyError if the path doesn't exist."""
    path = path.strip()
    if path in ("", "$"):
        return obj
    path = path[1:] if path.startswith("$") else path
    for token in _PATH_TOKEN_RE.findall(path):
        try:
            if token.startswith("["):
                obj = obj[int(token[1:-1])]
            elif isinstance(obj, dict):
                obj = obj[token]
            else:
                raise KeyError(token)
        except (IndexError, KeyError, TypeError) as exc:
            raise KeyError(f"path '{path}' not found (stopped at '{token}')") from exc
    return obj


def _equal(actual: Any, wanted: Any) -> bool:
    return actual == wanted or str(actual) == str(wanted)


def _check_json_expectations(document: Any, expect_json: dict[str, Any]) -> None:
    for path, wanted in expect_json.items():
        try:
            actual = get_path(document, path)
        except KeyError as exc:
            raise StepAssertionFailed(f"expected {path} == {wanted!r}, but {exc}") from exc
        if not _equal(actual, wanted):
            raise StepAssertionFailed(f"expected {path} == {wanted!r}, got {actual!r}")


def _apply_captures(document: Any, capture: dict[str, str], ctx: VariableContext, extras: dict[str, Any]) -> None:
    """capture: {var_name: path}. A path starting with '$' walks the JSON document; the
    special names in `extras` (e.g. '$status', '$body', '$topic') are looked up first."""
    for var, path in capture.items():
        if path in extras:
            ctx.set(var, extras[path])
        else:
            try:
                ctx.set(var, get_path(document, path))
            except KeyError as exc:
                raise ValueError(f"capture '{var}': {exc}") from exc


def _cfg(step: TestStep, ctx: VariableContext) -> dict[str, Any]:
    if not step.config:
        raise ValueError(f"Step {step.step_no}: action '{step.action}' requires a 'config' block")
    return ctx.resolve(step.config)


def _parse_payload(raw: bytes | str) -> Any:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else raw
    try:
        return json.loads(text)
    except ValueError:
        return text


# --------------------------------------------------------------------------- REST

def _run_rest_call(step: TestStep, ctx: VariableContext) -> str:
    cfg = _cfg(step, ctx)
    method = str(cfg.get("method", "GET")).upper()
    url = cfg["url"]
    if cfg.get("params"):
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(cfg["params"])

    headers = {str(k): str(v) for k, v in (cfg.get("headers") or {}).items()}
    auth = cfg.get("auth") or {}
    if auth.get("type") == "bearer":
        headers["Authorization"] = f"Bearer {auth['token']}"
    elif auth.get("type") == "basic":
        token = base64.b64encode(f"{auth['username']}:{auth['password']}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    elif auth.get("type") == "header":
        headers[auth["name"]] = str(auth["value"])

    body = cfg.get("body")
    data: Optional[bytes] = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        else:
            data = str(body).encode("utf-8")
    headers.setdefault("Accept", "application/json")

    context = None
    if url.lower().startswith("https"):
        if cfg.get("verify_tls", True) is False:
            context = ssl._create_unverified_context()  # noqa: SLF001 - explicit opt-out for test environments
        else:  # trusts the OS certificate store + public roots + config.ca_bundle (a company root certificate)
            from framework import tlsconfig

            context = tlsconfig.make_ssl_context(tlsconfig.TlsSettings(ca_bundle=str(cfg.get("ca_bundle") or "")))

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=step.timeout_ms / 1000, context=context) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:  # a 4xx/5xx is a response, and may be the expected one
        status, raw = exc.code, exc.read()

    document = _parse_payload(raw)

    expect_status = cfg.get("expect_status")
    if expect_status is not None:
        allowed = expect_status if isinstance(expect_status, list) else [expect_status]
        if not any(_equal(status, s) for s in allowed):
            raise StepAssertionFailed(f"expected HTTP status {expect_status}, got {status}: {str(document)[:300]}")
    if cfg.get("expect_json"):
        _check_json_expectations(document, cfg["expect_json"])
    if cfg.get("capture"):
        _apply_captures(document, cfg["capture"], ctx, {"$status": status, "$body": document})
    return f"{method} {cfg['url']} -> {status}"


# --------------------------------------------------------------------------- message bus

class _Broker:
    """One subscription/publisher, per broker type. Connection details come from the
    step's inline config, so nothing is shared between steps except a named subscription."""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.messages: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self.error: Optional[str] = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, timeout: float) -> None:
        self._thread = threading.Thread(target=self._safe_run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self.stop()
            raise TimeoutError(f"bus_subscribe: no subscription established within {timeout:.0f}s")
        if self.error:
            raise RuntimeError(f"bus_subscribe failed: {self.error}")

    def _safe_run(self) -> None:
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001 - surfaced to the step via self.error
            self.error = f"{type(exc).__name__}: {exc}"
            self._ready.set()

    def _emit(self, topic: str, raw: bytes) -> None:
        self.messages.put({"topic": topic, "payload": _parse_payload(raw), "received_at": time.time()})

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:  # pragma: no cover - implemented per broker
        raise NotImplementedError

    def publish(self, topic: str, payload: bytes) -> None:  # pragma: no cover
        raise NotImplementedError


class _MqttBroker(_Broker):
    def _client(self):
        import paho.mqtt.client as mqtt

        cfg = self.cfg
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg.get("client_id", ""))
        except AttributeError:  # paho-mqtt 1.x
            client = mqtt.Client(client_id=cfg.get("client_id", ""))
        if cfg.get("username"):
            client.username_pw_set(cfg["username"], cfg.get("password"))
        if cfg.get("tls"):
            client.tls_set()
        return client

    def _run(self) -> None:
        cfg = self.cfg
        client = self._client()
        client.on_message = lambda _c, _u, msg: self._emit(msg.topic, msg.payload)
        client.on_subscribe = lambda *_a, **_k: self._ready.set()
        client.connect(cfg["host"], int(cfg.get("port", 8883 if cfg.get("tls") else 1883)))
        client.subscribe(cfg["topic"], qos=int(cfg.get("qos", 1)))
        client.loop_start()
        try:
            self._stop.wait()
        finally:
            client.loop_stop()
            client.disconnect()

    def publish(self, topic: str, payload: bytes) -> None:
        client = self._client()
        cfg = self.cfg
        client.connect(cfg["host"], int(cfg.get("port", 8883 if cfg.get("tls") else 1883)))
        client.loop_start()
        try:
            info = client.publish(topic, payload, qos=int(cfg.get("qos", 1)))
            info.wait_for_publish(timeout=10)
        finally:
            client.loop_stop()
            client.disconnect()


class _AmqpBroker(_Broker):
    def _connection(self):
        import pika

        cfg = self.cfg
        kwargs: dict[str, Any] = {
            "host": cfg["host"],
            "port": int(cfg.get("port", 5671 if cfg.get("tls") else 5672)),
            "virtual_host": cfg.get("vhost", "/"),
        }
        if cfg.get("username"):
            kwargs["credentials"] = pika.PlainCredentials(cfg["username"], cfg["password"])
        if cfg.get("tls"):
            kwargs["ssl_options"] = pika.SSLOptions(ssl.create_default_context())
        return pika.BlockingConnection(pika.ConnectionParameters(**kwargs))

    def _run(self) -> None:
        cfg = self.cfg
        connection = self._connection()
        try:
            channel = connection.channel()
            if cfg.get("queue"):
                queue_name = cfg["queue"]
                channel.queue_declare(queue=queue_name, passive=True)
            else:  # private queue bound to an exchange, deleted when the test disconnects
                queue_name = channel.queue_declare(queue="", exclusive=True).method.queue
                channel.queue_bind(queue=queue_name, exchange=cfg["exchange"], routing_key=cfg.get("routing_key", "#"))
            self._ready.set()
            for method, _props, body in channel.consume(queue_name, auto_ack=True, inactivity_timeout=0.5):
                if self._stop.is_set():
                    break
                if method is not None:
                    self._emit(method.routing_key or queue_name, body)
        finally:
            connection.close()

    def publish(self, topic: str, payload: bytes) -> None:
        connection = self._connection()
        try:
            connection.channel().basic_publish(
                exchange=self.cfg.get("exchange", ""), routing_key=topic, body=payload
            )
        finally:
            connection.close()


class _KafkaBroker(_Broker):
    def _common(self) -> dict[str, Any]:
        cfg = self.cfg
        kwargs: dict[str, Any] = {"bootstrap_servers": cfg.get("bootstrap_servers", f"{cfg['host']}:{cfg.get('port', 9092)}")}
        if cfg.get("tls"):
            kwargs["security_protocol"] = "SASL_SSL" if cfg.get("username") else "SSL"
        elif cfg.get("username"):
            kwargs["security_protocol"] = "SASL_PLAINTEXT"
        if cfg.get("username"):
            kwargs.update(
                sasl_mechanism=cfg.get("sasl_mechanism", "PLAIN"),
                sasl_plain_username=cfg["username"],
                sasl_plain_password=cfg["password"],
            )
        return kwargs

    def _run(self) -> None:
        from kafka import KafkaConsumer

        cfg = self.cfg
        consumer = KafkaConsumer(
            cfg["topic"], group_id=cfg.get("group_id"), auto_offset_reset="latest",
            enable_auto_commit=False, consumer_timeout_ms=500, **self._common(),
        )
        try:
            deadline = time.monotonic() + 30
            while not consumer.assignment() and time.monotonic() < deadline:  # ready == partitions assigned
                consumer.poll(timeout_ms=200)
            self._ready.set()
            while not self._stop.is_set():
                for record in consumer.poll(timeout_ms=500).values():
                    for msg in record:
                        self._emit(msg.topic, msg.value)
        finally:
            consumer.close()

    def publish(self, topic: str, payload: bytes) -> None:
        from kafka import KafkaProducer

        producer = KafkaProducer(**self._common())
        try:
            producer.send(topic, payload).get(timeout=10)
            producer.flush()
        finally:
            producer.close()


_BROKERS = {"mqtt": _MqttBroker, "amqp": _AmqpBroker, "kafka": _KafkaBroker}


def _broker_for(cfg: dict[str, Any]) -> _Broker:
    kind = str(cfg.get("broker", "")).lower()
    if kind not in _BROKERS:
        raise ValueError(f"config.broker must be one of {sorted(_BROKERS)}, got {kind!r}")
    return _BROKERS[kind](cfg)


def _subscriptions(ctx: VariableContext) -> dict[str, dict[str, Any]]:
    if not hasattr(ctx, "bus_subscriptions"):
        ctx.bus_subscriptions = {}  # name -> {"broker": _Broker, "pending": [messages received, unconsumed]}
    return ctx.bus_subscriptions


def close_subscriptions(ctx: VariableContext) -> None:
    for sub in getattr(ctx, "bus_subscriptions", {}).values():
        sub["broker"].stop()
    ctx.bus_subscriptions = {}


def _run_bus_subscribe(step: TestStep, ctx: VariableContext) -> str:
    cfg = _cfg(step, ctx)
    name = cfg.get("name", "default")
    subs = _subscriptions(ctx)
    if name in subs:
        subs.pop(name)["broker"].stop()
    broker = _broker_for(cfg)
    broker.start(timeout=step.timeout_ms / 1000)
    subs[name] = {"broker": broker, "pending": []}
    return f"subscribed '{name}' ({cfg['broker']}:{cfg.get('topic') or cfg.get('queue') or cfg.get('routing_key')})"


def _matches(message: dict[str, Any], cfg: dict[str, Any]) -> bool:
    if cfg.get("topic_contains") and cfg["topic_contains"] not in message["topic"]:
        return False
    for path, wanted in (cfg.get("match") or {}).items():
        try:
            if not _equal(get_path(message["payload"], path), wanted):
                return False
        except KeyError:
            return False
    return True


def _run_bus_wait(step: TestStep, ctx: VariableContext) -> str:
    cfg = _cfg(step, ctx)
    name = cfg.get("name", "default")
    sub = _subscriptions(ctx).get(name)
    if sub is None:
        raise ValueError(f"bus_wait: no subscription named '{name}' -- add a bus_subscribe step earlier in the case")

    broker: _Broker = sub["broker"]
    deadline = time.monotonic() + step.timeout_ms / 1000
    seen = 0
    while True:
        # everything received since the subscribe (including before this wait) is eligible
        while True:
            try:
                sub["pending"].append(broker.messages.get_nowait())
            except queue.Empty:
                break
        for i, message in enumerate(sub["pending"]):
            if _matches(message, cfg):
                del sub["pending"][i]
                document = message["payload"]
                if cfg.get("expect_json"):
                    _check_json_expectations(document, cfg["expect_json"])
                if cfg.get("capture"):
                    _apply_captures(document, cfg["capture"], ctx, {"$topic": message["topic"], "$body": document})
                return f"received on {message['topic']}"
        seen = len(sub["pending"])
        if broker.error:
            raise RuntimeError(f"bus_wait: subscription '{name}' failed: {broker.error}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StepAssertionFailed(
                f"no message matching {cfg.get('match') or 'any'} on subscription '{name}' within "
                f"{step.timeout_ms} ms ({seen} other message(s) received)"
            )
        time.sleep(min(0.1, remaining))


def _run_bus_publish(step: TestStep, ctx: VariableContext) -> str:
    cfg = _cfg(step, ctx)
    payload = cfg.get("payload", "")
    raw = json.dumps(payload).encode("utf-8") if isinstance(payload, (dict, list)) else str(payload).encode("utf-8")
    topic = cfg.get("topic") or cfg.get("routing_key")
    if not topic:
        raise ValueError("bus_publish: config.topic (or routing_key for amqp) is required")
    _broker_for(cfg).publish(topic, raw)
    return f"published to {topic}"


_HANDLERS = {
    "rest_call": _run_rest_call,
    "bus_subscribe": _run_bus_subscribe,
    "bus_wait": _run_bus_wait,
    "bus_publish": _run_bus_publish,
}


def run_integration_action(step: TestStep, ctx: VariableContext) -> str:
    """Runs a rest_call / bus_* step. Returns a short human summary for the result record;
    raises StepAssertionFailed for a failed expectation, anything else for a real error."""
    return _HANDLERS[step.action](step, ctx)
