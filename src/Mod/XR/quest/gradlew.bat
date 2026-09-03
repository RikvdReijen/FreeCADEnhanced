@rem SPDX-License-Identifier: Apache-2.0
@rem Gradle start up script for Windows.
@rem gradle\wrapper\gradle-wrapper.jar is not checked in; run
@rem "gradle wrapper --gradle-version 8.7" once, or open the project in
@rem Android Studio, to create it.
@if "%DEBUG%"=="" @echo off
setlocal

set DIRNAME=%~dp0
if "%DIRNAME%"=="" set DIRNAME=.
set APP_HOME=%DIRNAME%
set WRAPPER_JAR=%APP_HOME%gradle\wrapper\gradle-wrapper.jar

if defined JAVA_HOME goto findJavaFromJavaHome
set JAVA_EXE=java.exe
%JAVA_EXE% -version >NUL 2>&1
if %ERRORLEVEL% equ 0 goto checkJar
echo ERROR: no java on PATH and JAVA_HOME is not set. 1>&2
exit /b 1

:findJavaFromJavaHome
set JAVA_EXE=%JAVA_HOME%\bin\java.exe
if exist "%JAVA_EXE%" goto checkJar
echo ERROR: JAVA_HOME is set to an invalid directory: %JAVA_HOME% 1>&2
exit /b 1

:checkJar
if exist "%WRAPPER_JAR%" goto execute
echo ERROR: %WRAPPER_JAR% is missing. 1>&2
echo Run "gradle wrapper --gradle-version 8.7" in this directory once. 1>&2
exit /b 1

:execute
"%JAVA_EXE%" %JAVA_OPTS% %GRADLE_OPTS% "-Dorg.gradle.appname=%~n0" -classpath "%WRAPPER_JAR%" org.gradle.wrapper.GradleWrapperMain %*
endlocal
