// SPDX-License-Identifier: LGPL-2.1-or-later
package org.freecad.xr;

import android.content.Context;
import android.net.Uri;
import android.util.Log;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;

/** Reads a picked document and hands it to the native side. */
final class ImportService {
    private static final String TAG = "FreeCADXR";
    private static final long MAX_BYTES = 256L * 1024L * 1024L;

    private ImportService() {}

    static void read(Context context, Uri uri) {
        final String name = FilePickerActivity.displayName(context, uri);
        try (InputStream input = context.getContentResolver().openInputStream(uri)) {
            if (input == null) {
                JniBridge.nativeToast("cannot open that file");
                return;
            }
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[64 * 1024];
            long total = 0;
            int read;
            while ((read = input.read(buffer)) > 0) {
                total += read;
                if (total > MAX_BYTES) {
                    JniBridge.nativeToast("that file is too large");
                    return;
                }
                out.write(buffer, 0, read);
            }
            Log.i(TAG, "imported " + name + " (" + total + " bytes)");
            JniBridge.nativeFileImported(name, out.toByteArray());
        } catch (Exception e) {
            Log.e(TAG, "import failed", e);
            JniBridge.nativeToast("import failed: " + e.getMessage());
        }
    }
}
