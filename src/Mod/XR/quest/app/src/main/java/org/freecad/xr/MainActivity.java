// SPDX-License-Identifier: LGPL-2.1-or-later
package org.freecad.xr;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.WindowManager;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The VR activity.
 *
 * <p>ARCHITECTURE.md §5 asks for a plain {@code Activity} rather than a
 * {@code NativeActivity}: OpenXR owns the display, so this activity never needs
 * a surface. It starts the native render thread, forwards the lifecycle to it,
 * and provides the few services that are easier in Java — the storage paths,
 * the SAF file picker, Google Drive and toasts.
 */
public class MainActivity extends Activity {
    private static final String TAG = "FreeCADXR";
    static final int REQUEST_PICK_FILE = 0x0F0F;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private GoogleDriveAuth driveAuth;
    private GoogleDriveClient driveClient;
    private boolean nativeStarted;

    static {
        System.loadLibrary("freecadxr");
    }

    // ---- native entry points (implemented in main.cpp) --------------------
    private native void nativeOnCreate(Object assetManager, String filesDir, String cacheDir);
    private native void nativeOnResume();
    private native void nativeOnPause();
    private native void nativeOnDestroy();
    private native boolean nativeIsRunning();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        driveAuth = new GoogleDriveAuth(this);
        driveClient = new GoogleDriveClient(driveAuth);

        // The headset needs no runtime permissions for anything this app does
        // by default; INTERNET is install time and storage goes through SAF.
        // Hand tracking is optional and asked for only when it is declared.
        requestOptionalPermissions();

        nativeOnCreate(getAssets(), getFilesDir().getAbsolutePath(),
                getCacheDir().getAbsolutePath());
        nativeStarted = true;
        Log.i(TAG, "native render thread started");
    }

    private void requestOptionalPermissions() {
        final List<String> wanted = new ArrayList<>();
        final String handTracking = "com.oculus.permission.HAND_TRACKING";
        if (checkSelfPermission(handTracking) != PackageManager.PERMISSION_GRANTED) {
            wanted.add(handTracking);
        }
        if (!wanted.isEmpty() && Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            requestPermissions(wanted.toArray(new String[0]), 1);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (nativeStarted) nativeOnResume();
    }

    @Override
    protected void onPause() {
        if (nativeStarted) nativeOnPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (nativeStarted) {
            nativeOnDestroy();
            nativeStarted = false;
        }
        io.shutdown();
        super.onDestroy();
    }

    // ---- services the native side calls ----------------------------------

    /** Called from native: shows a short message on the flat screen too. */
    @SuppressWarnings("unused")
    public void showToast(final String message) {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                Toast.makeText(MainActivity.this, message, Toast.LENGTH_SHORT).show();
            }
        });
    }

    /** Called from native: opens the SAF picker for a .fcxr or .FCStd file. */
    @SuppressWarnings("unused")
    public void openFilePicker() {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                startActivity(new Intent(MainActivity.this, FilePickerActivity.class));
            }
        });
    }

    @SuppressWarnings("unused")
    public void driveSignIn() {
        io.execute(new Runnable() {
            @Override
            public void run() {
                driveAuth.startDeviceFlow();
            }
        });
    }

    @SuppressWarnings("unused")
    public void driveListFiles() {
        io.execute(new Runnable() {
            @Override
            public void run() {
                driveClient.listFcxrFiles();
            }
        });
    }

    @SuppressWarnings("unused")
    public void driveDownload(final String name) {
        io.execute(new Runnable() {
            @Override
            public void run() {
                driveClient.downloadByName(name);
            }
        });
    }

    @SuppressWarnings("unused")
    public void driveUpload(final String name, final byte[] data) {
        io.execute(new Runnable() {
            @Override
            public void run() {
                driveClient.upload(name, data);
            }
        });
    }

}
