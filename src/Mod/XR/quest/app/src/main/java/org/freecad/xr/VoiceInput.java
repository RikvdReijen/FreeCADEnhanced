// SPDX-License-Identifier: LGPL-2.1-or-later
package org.freecad.xr;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.util.Log;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;

/**
 * Voice as a modelling input on the headset.
 *
 * Uses Android's on-device {@link SpeechRecognizer} (the Quest system provides
 * one) and posts every final transcript — and, at low confidence, partial
 * ones — to the desktop's sync server as {@code POST /api/v1/voice}. The
 * grammar, the dispatch and the action all live on the desktop
 * ({@code xrvoice}); the headset only listens and forwards, so the two sides
 * cannot disagree about what "fillet two millimetres" means.
 *
 * Needs {@code android.permission.RECORD_AUDIO}. Untested on a device: written
 * to the Android API, not run on a Quest.
 */
public final class VoiceInput {
    private static final String TAG = "FreeCADXR.Voice";

    private final Activity activity;
    private SpeechRecognizer recognizer;
    private String serverBase;   // e.g. http://192.168.1.10:47810
    private String token;
    private boolean listening;
    private boolean continuous = true;

    public VoiceInput(Activity activity) {
        this.activity = activity;
    }

    public void configure(String serverBase, String token) {
        this.serverBase = serverBase;
        this.token = token;
    }

    public boolean isAvailable() {
        return SpeechRecognizer.isRecognitionAvailable(activity);
    }

    public boolean isListening() {
        return listening;
    }

    public void start() {
        if (listening) return;
        if (!isAvailable()) {
            Log.w(TAG, "no speech recogniser on this device");
            return;
        }
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                if (recognizer == null) {
                    recognizer = SpeechRecognizer.createSpeechRecognizer(activity);
                    recognizer.setRecognitionListener(listener);
                }
                listening = true;
                recognizer.startListening(intent());
            }
        });
    }

    public void stop() {
        listening = false;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                if (recognizer != null) {
                    recognizer.stopListening();
                    recognizer.destroy();
                    recognizer = null;
                }
            }
        });
    }

    private Intent intent() {
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
        intent.putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true);
        return intent;
    }

    private final RecognitionListener listener = new RecognitionListener() {
        @Override public void onReadyForSpeech(Bundle params) {}
        @Override public void onBeginningOfSpeech() {}
        @Override public void onRmsChanged(float rmsdB) {}
        @Override public void onBufferReceived(byte[] buffer) {}
        @Override public void onEndOfSpeech() {}
        @Override public void onEvent(int eventType, Bundle params) {}

        @Override
        public void onError(int error) {
            Log.d(TAG, "recogniser error " + error);
            if (listening && continuous && recognizer != null) {
                recognizer.startListening(intent());
            }
        }

        @Override
        public void onPartialResults(Bundle partialResults) {
            ArrayList<String> texts = partialResults.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
            if (texts != null && !texts.isEmpty()) {
                post(texts.get(0), 0.5f, false);
            }
        }

        @Override
        public void onResults(Bundle results) {
            ArrayList<String> texts = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
            float[] scores = results.getFloatArray(SpeechRecognizer.CONFIDENCE_SCORES);
            if (texts != null && !texts.isEmpty()) {
                float confidence = (scores != null && scores.length > 0 && scores[0] >= 0f) ? scores[0] : 1.0f;
                post(texts.get(0), confidence, true);
            }
            if (listening && continuous && recognizer != null) {
                recognizer.startListening(intent());
            }
        }
    };

    /** Send a transcript to the desktop on a worker thread. */
    void post(final String text, final float confidence, final boolean isFinal) {
        if (serverBase == null || text == null || text.isEmpty()) return;
        new Thread(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection connection = null;
                try {
                    URL url = new URL(serverBase + "/api/v1/voice");
                    connection = (HttpURLConnection) url.openConnection();
                    connection.setRequestMethod("POST");
                    connection.setConnectTimeout(2000);
                    connection.setReadTimeout(2000);
                    connection.setDoOutput(true);
                    connection.setRequestProperty("Content-Type", "application/json");
                    if (token != null) connection.setRequestProperty("Authorization", "Bearer " + token);
                    String body = "{\"text\":" + JsonUtil.quote(text) + ",\"confidence\":" + confidence
                            + ",\"final\":" + isFinal + ",\"language\":\"en\"}";
                    OutputStream out = connection.getOutputStream();
                    out.write(body.getBytes(StandardCharsets.UTF_8));
                    out.close();
                    int status = connection.getResponseCode();
                    if (status != 200) Log.w(TAG, "voice post returned " + status);
                } catch (Exception exc) {
                    Log.w(TAG, "voice post failed: " + exc);
                } finally {
                    if (connection != null) connection.disconnect();
                }
            }
        }, "xr-voice-post").start();
    }

    /** Minimal JSON string quoting, enough for a transcript. */
    static final class JsonUtil {
        static String quote(String s) {
            StringBuilder sb = new StringBuilder("\"");
            for (int i = 0; i < s.length(); i++) {
                char c = s.charAt(i);
                switch (c) {
                    case '"': sb.append("\\\""); break;
                    case '\\': sb.append("\\\\"); break;
                    case '\n': sb.append("\\n"); break;
                    case '\r': sb.append("\\r"); break;
                    case '\t': sb.append("\\t"); break;
                    default:
                        if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                        else sb.append(c);
                }
            }
            return sb.append('"').toString();
        }
    }
}
