// SPDX-License-Identifier: LGPL-2.1-or-later
package org.freecad.xr;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.provider.OpenableColumns;

/**
 * A transparent shim that opens the Storage Access Framework picker.
 *
 * <p>The VR activity cannot show the picker itself (it is running an immersive
 * OpenXR session), so this trampoline activity opens it, hands the chosen URI
 * back to {@link MainActivity} and finishes. On Quest the picker appears as a
 * flat panel over the app.
 */
public class FilePickerActivity extends Activity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        // .fcxr has no registered MIME type, so accept everything and filter on
        // the name; .FCStd files are offered too because the desktop can
        // convert them on the way in.
        intent.setType("*/*");
        intent.putExtra(Intent.EXTRA_MIME_TYPES,
                new String[] {"application/x-fcxr", "application/octet-stream", "*/*"});
        startActivityForResult(intent, MainActivity.REQUEST_PICK_FILE);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == MainActivity.REQUEST_PICK_FILE && resultCode == RESULT_OK &&
                data != null && data.getData() != null) {
            final Uri uri = data.getData();
            // Persist the permission so a re-import after a restart still works.
            try {
                getContentResolver().takePersistableUriPermission(
                        uri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
            } catch (SecurityException ignored) {
                // Not all providers grant persistable permissions.
            }
            new Thread(new Runnable() {
                @Override
                public void run() {
                    ImportService.read(getApplicationContext(), uri);
                }
            }).start();
        }
        finish();
    }

    /** Best effort display name for a content URI. */
    public static String displayName(Context context, Uri uri) {
        String name = uri.getLastPathSegment();
        try (Cursor cursor = context.getContentResolver().query(uri, null, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (index >= 0) name = cursor.getString(index);
            }
        } catch (Exception ignored) {
            // fall through to the path segment
        }
        if (name == null || name.isEmpty()) name = "imported.fcxr";
        final int slash = name.lastIndexOf('/');
        return slash >= 0 ? name.substring(slash + 1) : name;
    }
}
