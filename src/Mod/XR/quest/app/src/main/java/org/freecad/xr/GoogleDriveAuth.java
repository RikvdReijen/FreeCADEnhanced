// SPDX-License-Identifier: LGPL-2.1-or-later
package org.freecad.xr;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;

import androidx.security.crypto.EncryptedSharedPreferences;
import androidx.security.crypto.MasterKey;

import org.json.JSONObject;

import java.io.IOException;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;

/**
 * OAuth 2.0 for a device with no keyboard: the limited-input device flow.
 *
 * <p>The headset asks Google for a short user code, the code and a URL are
 * shown on a panel in VR, the user types them on a phone or laptop, and this
 * class polls the token endpoint until the grant appears. Refresh tokens are
 * kept in {@link EncryptedSharedPreferences} so the sign in survives a restart.
 *
 * <p>The client id is <em>never</em> hard coded: it comes from
 * {@code BuildConfig.GOOGLE_CLIENT_ID}, which Gradle reads from
 * {@code local.properties} (see the README). With no id configured the Drive
 * panel simply reports that Drive is not set up.
 */
public class GoogleDriveAuth {
    private static final String TAG = "FreeCADXR";
    private static final String DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code";
    private static final String TOKEN_URL = "https://oauth2.googleapis.com/token";
    private static final String PREFS = "drive_tokens";
    private static final String KEY_REFRESH = "refresh_token";

    private final Context context;
    private SharedPreferences prefs;
    private String accessToken;
    private long accessExpiryMillis;
    private volatile boolean flowRunning;

    public GoogleDriveAuth(Context context) {
        this.context = context.getApplicationContext();
        this.prefs = openPreferences();
    }

    private SharedPreferences openPreferences() {
        try {
            MasterKey key = new MasterKey.Builder(context)
                    .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                    .build();
            return EncryptedSharedPreferences.create(
                    context, PREFS, key,
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM);
        } catch (Exception e) {
            // Keystore trouble should not brick the app; fall back to plain
            // preferences and say so, because the refresh token is sensitive.
            Log.e(TAG, "EncryptedSharedPreferences unavailable, falling back", e);
            JniBridge.nativeToast("Drive tokens are not encrypted on this device");
            return context.getSharedPreferences(PREFS + "_plain", Context.MODE_PRIVATE);
        }
    }

    public boolean isConfigured() {
        return BuildConfig.GOOGLE_CLIENT_ID != null && !BuildConfig.GOOGLE_CLIENT_ID.isEmpty();
    }

    public boolean isSignedIn() {
        return prefs.getString(KEY_REFRESH, null) != null;
    }

    public void signOut() {
        prefs.edit().remove(KEY_REFRESH).apply();
        accessToken = null;
        accessExpiryMillis = 0;
        JniBridge.nativeDriveAuthState("signed out", "");
    }

    /**
     * Returns a valid access token, refreshing it when needed, or null when the
     * user has not signed in yet. Call from a worker thread.
     */
    public synchronized String accessToken() {
        if (accessToken != null && System.currentTimeMillis() < accessExpiryMillis - 30_000L) {
            return accessToken;
        }
        String refresh = prefs.getString(KEY_REFRESH, null);
        if (refresh == null) return null;
        try {
            StringBuilder body = new StringBuilder();
            body.append("client_id=").append(encode(BuildConfig.GOOGLE_CLIENT_ID));
            if (!BuildConfig.GOOGLE_CLIENT_SECRET.isEmpty()) {
                body.append("&client_secret=").append(encode(BuildConfig.GOOGLE_CLIENT_SECRET));
            }
            body.append("&refresh_token=").append(encode(refresh));
            body.append("&grant_type=refresh_token");
            JSONObject response = postForm(TOKEN_URL, body.toString());
            if (response == null || !response.has("access_token")) {
                Log.w(TAG, "refresh failed: " + response);
                signOut();
                return null;
            }
            accessToken = response.getString("access_token");
            accessExpiryMillis =
                    System.currentTimeMillis() + response.optLong("expires_in", 3600L) * 1000L;
            return accessToken;
        } catch (Exception e) {
            Log.e(TAG, "token refresh failed", e);
            return null;
        }
    }

    /** Runs the whole device flow. Blocks; call from a worker thread. */
    public void startDeviceFlow() {
        if (!isConfigured()) {
            JniBridge.nativeDriveAuthState(
                    "Drive is not configured (set GOOGLE_CLIENT_ID)", "");
            return;
        }
        if (flowRunning) return;
        flowRunning = true;
        try {
            String body = "client_id=" + encode(BuildConfig.GOOGLE_CLIENT_ID) +
                    "&scope=" + encode(BuildConfig.DRIVE_SCOPE);
            JSONObject start = postForm(DEVICE_CODE_URL, body);
            if (start == null || !start.has("device_code")) {
                JniBridge.nativeDriveAuthState("could not start sign in", "");
                return;
            }
            final String deviceCode = start.getString("device_code");
            final String userCode = start.getString("user_code");
            final String url = start.optString("verification_url",
                    start.optString("verification_uri", "https://www.google.com/device"));
            long intervalSeconds = start.optLong("interval", 5L);
            final long deadline =
                    System.currentTimeMillis() + start.optLong("expires_in", 900L) * 1000L;

            JniBridge.nativeDriveAuthState(url, userCode);

            while (System.currentTimeMillis() < deadline) {
                Thread.sleep(intervalSeconds * 1000L);
                StringBuilder poll = new StringBuilder();
                poll.append("client_id=").append(encode(BuildConfig.GOOGLE_CLIENT_ID));
                if (!BuildConfig.GOOGLE_CLIENT_SECRET.isEmpty()) {
                    poll.append("&client_secret=")
                        .append(encode(BuildConfig.GOOGLE_CLIENT_SECRET));
                }
                poll.append("&device_code=").append(encode(deviceCode));
                poll.append("&grant_type=urn:ietf:params:oauth:grant-type:device_code");
                JSONObject response = postForm(TOKEN_URL, poll.toString());
                if (response == null) continue;
                if (response.has("access_token")) {
                    accessToken = response.getString("access_token");
                    accessExpiryMillis = System.currentTimeMillis() +
                            response.optLong("expires_in", 3600L) * 1000L;
                    String refresh = response.optString("refresh_token", null);
                    if (refresh != null) prefs.edit().putString(KEY_REFRESH, refresh).apply();
                    JniBridge.nativeDriveAuthState("signed in", "");
                    return;
                }
                String error = response.optString("error", "");
                if ("authorization_pending".equals(error)) {
                    continue;
                } else if ("slow_down".equals(error)) {
                    intervalSeconds += 5L;
                } else if ("expired_token".equals(error)) {
                    JniBridge.nativeDriveAuthState("the code expired, try again", "");
                    return;
                } else if ("access_denied".equals(error)) {
                    JniBridge.nativeDriveAuthState("sign in was refused", "");
                    return;
                } else if (!error.isEmpty()) {
                    JniBridge.nativeDriveAuthState("sign in failed: " + error, "");
                    return;
                }
            }
            JniBridge.nativeDriveAuthState("the code expired, try again", "");
        } catch (Exception e) {
            Log.e(TAG, "device flow failed", e);
            JniBridge.nativeDriveAuthState("sign in failed: " + e.getMessage(), "");
        } finally {
            flowRunning = false;
        }
    }

    // ---- helpers ---------------------------------------------------------

    private static String encode(String value) {
        try {
            return URLEncoder.encode(value, "UTF-8");
        } catch (IOException e) {
            return value;
        }
    }

    private static JSONObject postForm(String url, String body) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(url).openConnection();
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(20000);
            connection.setRequestProperty("Content-Type",
                    "application/x-www-form-urlencoded; charset=UTF-8");
            byte[] payload = body.getBytes("UTF-8");
            connection.setFixedLengthStreamingMode(payload.length);
            try (OutputStream out = connection.getOutputStream()) {
                out.write(payload);
            }
            String text = GoogleDriveClient.readAll(connection);
            if (text == null || text.isEmpty()) return null;
            return new JSONObject(text);
        } catch (Exception e) {
            Log.w(TAG, "POST " + url + " failed: " + e);
            return null;
        } finally {
            if (connection != null) connection.disconnect();
        }
    }
}
