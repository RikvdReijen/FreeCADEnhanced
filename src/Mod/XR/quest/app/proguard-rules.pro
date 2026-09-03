# SPDX-License-Identifier: LGPL-2.1-or-later
#
# The native side looks these up by name through JNI, so R8 must not rename or
# remove them.
-keepclasseswithmembernames class * {
    native <methods>;
}
-keep class org.freecad.xr.JniBridge { *; }
-keep class org.freecad.xr.MainActivity {
    public void showToast(java.lang.String);
    public void openFilePicker();
    public void driveSignIn();
    public void driveListFiles();
    public void driveDownload(java.lang.String);
    public void driveUpload(java.lang.String, byte[]);
}
-keep class org.freecad.xr.BuildConfig { *; }

# org.json ships with the platform.
-dontwarn org.json.**
