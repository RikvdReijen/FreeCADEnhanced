// SPDX-License-Identifier: LGPL-2.1-or-later
package org.freecad.xr;

/**
 * The Java to native surface.
 *
 * <p>Every method here is implemented in {@code jni_bridge.cpp} and only ever
 * enqueues work for the render thread, so all of them are safe to call from the
 * Android main thread or from a background worker.
 */
public final class JniBridge {
    private JniBridge() {}

    /** A {@code .fcxr} file the user picked with the system file picker. */
    public static native void nativeFileImported(String name, byte[] data);

    /** The Drive file listing, as a JSON array of {@code {"id","name","size"}}. */
    public static native void nativeDriveFiles(String json);

    /**
     * Progress of the OAuth device flow.
     *
     * @param status   human readable status shown on the Drive panel
     * @param userCode the code to type on another device, or "" once signed in
     */
    public static native void nativeDriveAuthState(String status, String userCode);

    /** A file downloaded from Drive. */
    public static native void nativeDriveDownloaded(String name, byte[] data);

    /** A short message for the wrist panel. */
    public static native void nativeToast(String message);
}
