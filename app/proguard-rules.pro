# ProGuard rules for Download Manager FS
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
-keep class org.fsgroupz.downloadmanagerfs.MainActivity$AndroidBridge { *; }
