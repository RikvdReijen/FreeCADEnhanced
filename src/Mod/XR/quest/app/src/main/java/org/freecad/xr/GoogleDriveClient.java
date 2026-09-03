// SPDX-License-Identifier: LGPL-2.1-or-later
package org.freecad.xr;

import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.util.HashMap;
import java.util.Map;

/**
 * Drive v3 over {@link HttpURLConnection}: list, download and upload of
 * {@code .fcxr} packages. No Google client library, so the APK stays small and
 * the only dependency is the platform's HTTP stack.
 *
 * <p>The default scope is {@code drive.file}, which sees only the files this
 * app (or the desktop workbench signed in with the same OAuth client) created.
 * That keeps the app out of Google's restricted scope review; widen it in
 * {@code local.properties} if you want to browse a whole Drive.
 */
public class GoogleDriveClient {
    private static final String TAG = "FreeCADXR";
    private static final String FILES_URL = "https://www.googleapis.com/drive/v3/files";
    private static final String UPLOAD_URL =
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart";
    private static final long MAX_DOWNLOAD = 256L * 1024L * 1024L;

    private final GoogleDriveAuth auth;
    /** name -> file id, from the most recent listing. */
    private final Map<String, String> idsByName = new HashMap<>();

    public GoogleDriveClient(GoogleDriveAuth auth) {
        this.auth = auth;
    }

    /** Lists .fcxr files, newest first, and posts the result to native. */
    public void listFcxrFiles() {
        String token = auth.accessToken();
        if (token == null) {
            JniBridge.nativeDriveAuthState(
                    auth.isConfigured() ? "not signed in" : "Drive is not configured", "");
            JniBridge.nativeDriveFiles("[]");
            return;
        }
        HttpURLConnection connection = null;
        try {
            // Drive has no MIME type for .fcxr, so match on the name.
            String query = "name contains '.fcxr' and trashed = false";
            String url = FILES_URL +
                    "?q=" + URLEncoder.encode(query, "UTF-8") +
                    "&orderBy=modifiedTime%20desc" +
                    "&pageSize=100" +
                    "&fields=" + URLEncoder.encode("files(id,name,size,modifiedTime)", "UTF-8");
            connection = (HttpURLConnection) new URL(url).openConnection();
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(30000);
            String text = readAll(connection);
            if (text == null) {
                JniBridge.nativeDriveFiles("[]");
                return;
            }
            JSONObject root = new JSONObject(text);
            JSONArray files = root.optJSONArray("files");
            if (files == null) files = new JSONArray();
            idsByName.clear();
            JSONArray out = new JSONArray();
            for (int i = 0; i < files.length(); ++i) {
                JSONObject file = files.getJSONObject(i);
                final String name = file.optString("name", "");
                final String id = file.optString("id", "");
                if (name.isEmpty() || id.isEmpty()) continue;
                idsByName.put(name, id);
                JSONObject entry = new JSONObject();
                entry.put("id", id);
                entry.put("name", name);
                entry.put("size", file.optString("size", "0"));
                out.put(entry);
            }
            Log.i(TAG, "Drive listed " + out.length() + " file(s)");
            JniBridge.nativeDriveFiles(out.toString());
        } catch (Exception e) {
            Log.e(TAG, "Drive list failed", e);
            JniBridge.nativeDriveAuthState("Drive list failed: " + e.getMessage(), "");
            JniBridge.nativeDriveFiles("[]");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    /** Downloads a file from the most recent listing, by display name. */
    public void downloadByName(String name) {
        final String id = idsByName.get(name);
        if (id == null) {
            JniBridge.nativeToast("refresh the Drive list first");
            return;
        }
        String token = auth.accessToken();
        if (token == null) {
            JniBridge.nativeToast("not signed in to Drive");
            return;
        }
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(FILES_URL + "/" + id + "?alt=media")
                    .openConnection();
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(120000);
            final int status = connection.getResponseCode();
            if (status != 200) {
                JniBridge.nativeToast("Drive download failed (HTTP " + status + ")");
                return;
            }
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            try (InputStream input = connection.getInputStream()) {
                byte[] buffer = new byte[64 * 1024];
                long total = 0;
                int read;
                while ((read = input.read(buffer)) > 0) {
                    total += read;
                    if (total > MAX_DOWNLOAD) {
                        JniBridge.nativeToast("that Drive file is too large");
                        return;
                    }
                    out.write(buffer, 0, read);
                }
            }
            Log.i(TAG, "Drive downloaded " + name + " (" + out.size() + " bytes)");
            JniBridge.nativeDriveDownloaded(name, out.toByteArray());
        } catch (Exception e) {
            Log.e(TAG, "Drive download failed", e);
            JniBridge.nativeToast("Drive download failed: " + e.getMessage());
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    /** Multipart upload of a new file into the Drive root. */
    public void upload(String name, byte[] data) {
        String token = auth.accessToken();
        if (token == null || data == null || data.length == 0) {
            JniBridge.nativeToast("not signed in to Drive");
            return;
        }
        final String boundary = "fcxr" + System.nanoTime();
        HttpURLConnection connection = null;
        try {
            JSONObject metadata = new JSONObject();
            metadata.put("name", name);
            final byte[] head = ("--" + boundary + "\r\n" +
                    "Content-Type: application/json; charset=UTF-8\r\n\r\n" +
                    metadata + "\r\n" +
                    "--" + boundary + "\r\n" +
                    "Content-Type: application/x-fcxr\r\n\r\n").getBytes("UTF-8");
            final byte[] tail = ("\r\n--" + boundary + "--\r\n").getBytes("UTF-8");

            connection = (HttpURLConnection) new URL(UPLOAD_URL).openConnection();
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(120000);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setRequestProperty("Content-Type",
                    "multipart/related; boundary=" + boundary);
            connection.setFixedLengthStreamingMode(head.length + data.length + tail.length);
            try (OutputStream out = connection.getOutputStream()) {
                out.write(head);
                out.write(data);
                out.write(tail);
            }
            final String text = readAll(connection);
            Log.i(TAG, "Drive upload replied: " + text);
            JniBridge.nativeToast("uploaded " + name + " to Drive");
        } catch (Exception e) {
            Log.e(TAG, "Drive upload failed", e);
            JniBridge.nativeToast("Drive upload failed: " + e.getMessage());
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    /** Reads a response body (or the error body) as UTF-8 text. */
    static String readAll(HttpURLConnection connection) {
        InputStream stream = null;
        try {
            final int status = connection.getResponseCode();
            stream = (status >= 400) ? connection.getErrorStream() : connection.getInputStream();
            if (stream == null) return "";
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int read;
            while ((read = stream.read(buffer)) > 0) out.write(buffer, 0, read);
            return out.toString("UTF-8");
        } catch (Exception e) {
            Log.w(TAG, "reading the response failed: " + e);
            return null;
        } finally {
            try {
                if (stream != null) stream.close();
            } catch (Exception ignored) {
                // nothing useful to do
            }
        }
    }
}
