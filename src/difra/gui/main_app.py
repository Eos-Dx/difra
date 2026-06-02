import logging
import os
import sys
from pathlib import Path
import ctypes

# CRITICAL: Workaround for Windows Application Control DLL blocking
# Must be done BEFORE any other imports that might load DLLs
if sys.platform == 'win32':
    try:
        # Set DLL directory to conda's Library\bin before ANY DLL loading occurs
        conda_lib_bin = Path(sys.executable).parent / "Library" / "bin"
        if conda_lib_bin.exists():
            # Try multiple approaches to set DLL search path
            ctypes.windll.kernel32.SetDllDirectoryW(str(conda_lib_bin))
            try:
                ctypes.windll.kernel32.AddDllDirectory(str(conda_lib_bin))
            except:
                pass
            os.environ['PATH'] = str(conda_lib_bin) + os.pathsep + os.environ.get('PATH', '')
    except Exception:
        pass  # Will fail with detailed error message later if PyQt5 can't load

# Ensure the standalone repo `src/` root is importable.
src_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(src_root))

# Add PIXet SDK path so pxcore.dll and its dependencies are discoverable.
pixet_sdk_path = os.environ.get("PIXET_SDK_PATH", r"C:\Program Files\PIXet Pro")
if os.path.isdir(pixet_sdk_path):
    # Add to Windows PATH for DLL loading before hardware initialization.
    os.environ['PATH'] = pixet_sdk_path + os.pathsep + os.environ.get('PATH', '')

kinesis_sdk_path = os.environ.get("KINESIS_SDK_PATH", r"C:\Program Files\Thorlabs\Kinesis")
if os.path.isdir(kinesis_sdk_path):
    # Add to Windows PATH for DLL loading
    os.environ['PATH'] = kinesis_sdk_path + os.pathsep + os.environ.get('PATH', '')
    # Add to sys.path for Python module discovery
    # While Kinesis might not have a direct Python module to import this way,
    # ensuring its DLLs are discoverable for ctypes or similar bindings is crucial.
    sys.path.insert(0, kinesis_sdk_path)

# Import Qt bindings with error handling for Application Control policies
try:
    from difra.gui.qt_compat import (
        DIALOG_ACCEPTED,
        QT_API,
        QtCore,
        QtWidgets,
        exec_app,
        exec_dialog,
    )

    QDate = QtCore.QDate
    QSettings = QtCore.QSettings
    QApplication = QtWidgets.QApplication
    QMessageBox = QtWidgets.QMessageBox
except ImportError as e:
    error_msg = str(e)
    if "Application Control policy" in error_msg or "DLL load failed" in error_msg:
        print("\n" + "="*80)
        print("ERROR: Windows Defender Application Control (WDAC) is blocking Qt")
        print("="*80)
        print(f"\nDetails: {error_msg}\n")
        conda_env_path = sys.executable.replace('python.exe', '').rstrip('\\\\')
        print("This application requires Qt bindings, but Windows WDAC policy is blocking the DLLs.")
        print("\nBLOCKED FILES:")
        print(f"  - {conda_env_path}\\Library\\bin\\Qt*.dll")
        print(f"  - {conda_env_path}\\Lib\\site-packages\\PyQt*\\*.pyd")
        print("\nRECOMMENDED SOLUTIONS (in order of preference):")
        print("\n1. Add conda environment to WDAC policy (requires Administrator):")
        print("   Run PowerShell as Administrator and execute:")
        print(f'   $rule = New-CIPolicyRule -Level FilePath -FilePath "{conda_env_path}\\Library\\bin"')
        print('   Set-RuleOption -FilePath "C:\\Windows\\System32\\CodeIntegrity\\CIPolicies\\Active\\{GUID}.cip" -Option 0')
        print("   (Replace {GUID} with your active policy GUID from Get-CIPolicyInfo)")
        print("\n2. Disable WDAC temporarily (requires Administrator + restart):")
        print("   a. Run: bcdedit /set {current} hypervisorlaunchtype off")
        print("   b. Run: mountvol X: /s")
        print("   c. Rename: X:\\EFI\\Microsoft\\Boot\\CIPolicies\\Active\\*.cip to *.cip.bak")
        print("   d. Restart computer")
        print("\n3. Contact your IT administrator with this information:")
        print("   Request: Add Miniconda/Anaconda Qt DLLs to WDAC allow list")
        print(f"   Paths: {conda_env_path}\\Library\\bin\\*.dll")
        print(f"          {conda_env_path}\\Lib\\site-packages\\PyQt*\\*.pyd")
        print("\nNOTE: Unblock-File and Set-ExecutionPolicy do NOT work with WDAC policies.")
        print("="*80 + "\n")
    else:
        print(f"\nERROR: Failed to import Qt bindings: {error_msg}\n")
    sys.exit(1)

from difra.gui.views.main_window import MainWindow
from difra.gui.daily_report_app_scheduler import (
    start_previous_day_report_on_startup,
    start_today_report_on_shutdown,
)
from difra.gui.views.welcome_dialog import WelcomeDialog
from difra.utils.logging_setup import (
    configure_third_party_logging,
    log_context,
    setup_logging,
)

# Setup enhanced logging
log_config = {
    "console_level": logging.INFO,
    "file_level": logging.DEBUG,
    "max_bytes": 20 * 1024 * 1024,  # 20MB
    "backup_count": 10,
}
log_path = setup_logging(config=log_config, structured=True)
configure_third_party_logging()

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    try:
        with log_context(
            session_id=f"session_{QDate.currentDate().toString('yyyy-MM-dd')}",
            hardware_state="initializing",
        ):
            logger.info("="*80)
            logger.info("DiFRA application starting")
            logger.info("="*80)
            logger.info(f"Python version: {sys.version}")
            logger.info(f"Qt binding: {QT_API}")
            logger.info(f"Log file path: {log_path}")
            logger.info(f"Working directory: {Path.cwd()}")
            logger.info(f"Source root: {src_root}")

            logger.info("Creating QApplication...")
            app = QApplication(sys.argv)
            logger.info("QApplication created successfully")

            # --- Welcome dialog with setup selection and embedded motivation ---
            # (Motivation popup removed; now shown inside Welcome dialog)

            # Always show Welcome dialog for setup selection before creating main window
            try:
                logger.info("Showing welcome dialog for setup selection")
                dlg = WelcomeDialog()
                logger.debug("Welcome dialog instance created")
                result = exec_dialog(dlg)
                if result != DIALOG_ACCEPTED:
                    logger.info("Welcome dialog canceled by user; exiting application")
                    sys.exit(0)
                logger.info("Welcome dialog completed successfully")
            except Exception as e:
                logger.error(f"Failed to show welcome dialog: {e}", exc_info=True)
                error_msg = (
                    f"Failed to show welcome dialog.\n\n"
                    f"Error: {type(e).__name__}: {e}\n\n"
                    f"This may indicate a problem with the Qt installation or system configuration.\n\n"
                    f"Check the log file for details: {log_path}"
                )
                QMessageBox.critical(None, "Application Error", error_msg)
                logger.critical("Application terminating due to welcome dialog error")
                sys.exit(1)

            try:
                logger.info("Creating main window...")
                win = MainWindow()
                logger.info("Main window created successfully")
                win.setWindowTitle("DiFRA")
                start_previous_day_report_on_startup(win.config)
                app.aboutToQuit.connect(lambda: start_today_report_on_shutdown(win.config))
                logger.info("Showing main window...")
                win.show()
                logger.info("Main window displayed")
            except Exception as e:
                logger.error(f"Failed to create or show main window: {e}", exc_info=True)
                error_msg = (
                    f"Failed to create main window.\n\n"
                    f"Error: {type(e).__name__}: {e}\n\n"
                    f"This may indicate a problem with the application configuration.\n\n"
                    f"Check the log file for details: {log_path}"
                )
                QMessageBox.critical(None, "Application Error", error_msg)
                logger.critical("Application terminating due to main window creation error")
                sys.exit(1)

            logger.info("="*80)
            logger.info("DiFRA application ready and running")
            logger.info("="*80)
            
            try:
                exit_code = exec_app(app)
                logger.info("="*80)
                logger.info(f"DiFRA application shutting down (exit code: {exit_code})")
                logger.info("="*80)
                sys.exit(exit_code)
            except Exception as e:
                logger.error(f"Error during application execution: {e}", exc_info=True)
                error_msg = (
                    f"Application crashed during execution.\n\n"
                    f"Error: {type(e).__name__}: {e}\n\n"
                    f"Check the log file for details: {log_path}"
                )
                QMessageBox.critical(None, "Application Error", error_msg)
                logger.critical("Application terminating due to runtime error")
                sys.exit(1)
                
    except Exception as e:
        # Top-level exception handler
        logger.critical(f"Unhandled exception in main: {e}", exc_info=True)
        error_msg = (
            f"Critical error in application startup.\n\n"
            f"Error: {type(e).__name__}: {e}\n\n"
            f"The application cannot continue.\n\n"
            f"Log file: {log_path if 'log_path' in locals() else 'Unknown'}"
        )
        try:
            QMessageBox.critical(None, "Critical Error", error_msg)
        except:
            print(f"\n{'='*80}")
            print("CRITICAL ERROR")
            print(f"{'-'*80}")
            print(error_msg)
            print(f"{'='*80}\n")
        sys.exit(1)
