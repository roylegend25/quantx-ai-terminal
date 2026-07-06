"""Small Keras sequence models (LSTM / CNN-LSTM / Transformer) sized for
CPU training on this 2-vCPU host. Imported only inside the worker
subprocess - TensorFlow must never load into the API process.

Input: sliding windows of `seq_len` consecutive feature vectors from the
market-history dataset (standardized with train-split statistics only).
Output: P(price up over the label horizon). Deliberately compact
architectures - the goal is honest trainability on this hardware, not
paper-scale networks that would OOM the trading backend.
"""

from __future__ import annotations

import numpy as np


def make_sequences(X: np.ndarray, y: np.ndarray, seq_len: int) -> tuple[np.ndarray, np.ndarray]:
    """Rows t-seq_len+1..t predict label at t. len(out) = len(X) - seq_len + 1."""
    if len(X) < seq_len:
        return np.empty((0, seq_len, X.shape[1])), np.empty((0,))
    windows = np.stack([X[i : i + seq_len] for i in range(len(X) - seq_len + 1)])
    return windows, y[seq_len - 1 :]


def configure_tensorflow():
    import tensorflow as tf

    tf.config.threading.set_intra_op_parallelism_threads(2)
    tf.config.threading.set_inter_op_parallelism_threads(1)
    return tf


def build_model(algorithm: str, seq_len: int, n_features: int, hp: dict):
    tf = configure_tensorflow()
    from tensorflow.keras import layers, models, optimizers

    inputs = layers.Input(shape=(seq_len, n_features))

    if algorithm == "lstm":
        x = layers.LSTM(int(hp.get("units", 32)))(inputs)
    elif algorithm == "cnn_lstm":
        x = layers.Conv1D(int(hp.get("filters", 16)), kernel_size=3, padding="causal", activation="relu")(inputs)
        x = layers.LSTM(int(hp.get("units", 32)))(x)
    elif algorithm == "transformer":
        d_model = int(hp.get("d_model", 32))
        heads = int(hp.get("heads", 2))
        x = layers.Dense(d_model)(inputs)
        attn = layers.MultiHeadAttention(num_heads=heads, key_dim=max(4, d_model // heads))(x, x)
        x = layers.LayerNormalization()(x + attn)
        ff = layers.Dense(d_model * 2, activation="relu")(x)
        ff = layers.Dense(d_model)(ff)
        x = layers.LayerNormalization()(x + ff)
        x = layers.GlobalAveragePooling1D()(x)
    else:
        raise ValueError(f"Unknown sequence algorithm '{algorithm}'")

    x = layers.Dropout(0.2)(x)
    x = layers.Dense(16, activation="relu")(x)
    outputs = layers.Dense(1, activation="sigmoid")(x)

    model = models.Model(inputs, outputs)
    model.compile(
        optimizer=optimizers.Adam(learning_rate=float(hp.get("learning_rate", 0.001))),
        loss="binary_crossentropy",
        metrics=["accuracy"],
    )
    _ = tf  # keep the configured module referenced
    return model


class EpochProgress:
    """Keras callback bridging epoch-level training state to the job
    progress writer (loss, val_loss, accuracy, lr, eta, samples/sec)."""

    def __init__(self, total_epochs: int, samples: int, report):
        import time

        self._time = time
        self.total_epochs = total_epochs
        self.samples = samples
        self.report = report
        self.epoch_started = None
        self.history: list[dict] = []

    def keras_callback(self):
        from tensorflow.keras.callbacks import Callback

        outer = self

        class _Bridge(Callback):
            def on_epoch_begin(self, epoch, logs=None):
                outer.epoch_started = outer._time.time()

            def on_epoch_end(self, epoch, logs=None):
                logs = logs or {}
                elapsed = max(1e-6, outer._time.time() - (outer.epoch_started or outer._time.time()))
                remaining = (outer.total_epochs - epoch - 1) * elapsed
                lr = float(self.model.optimizer.learning_rate.numpy())
                entry = {
                    "epoch": epoch + 1,
                    "loss": round(float(logs.get("loss", 0.0)), 5),
                    "val_loss": round(float(logs.get("val_loss")), 5) if logs.get("val_loss") is not None else None,
                    "accuracy": round(float(logs.get("accuracy", 0.0)), 4),
                    "val_accuracy": round(float(logs.get("val_accuracy")), 4) if logs.get("val_accuracy") is not None else None,
                }
                outer.history.append(entry)
                outer.report(
                    stage="training",
                    pct=30 + int(55 * (epoch + 1) / outer.total_epochs),
                    epoch=epoch + 1,
                    total_epochs=outer.total_epochs,
                    learning_rate=round(lr, 6),
                    eta_seconds=round(remaining, 1),
                    samples_per_sec=round(outer.samples / elapsed, 1),
                    **entry,
                )

        return _Bridge()
