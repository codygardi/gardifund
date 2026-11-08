# Keep the Python worker and any descendants tied to this launcher. Task Scheduler
# can terminate its PowerShell action without terminating a venv launcher's children.
function Enable-GardiFundProcessJob {
    if (-not ('GardiFundProcessJob' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class GardiFundProcessJob {
    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimits {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits {
        public BasicLimits BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")]
    private static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    // This handle deliberately remains open for the life of the launcher. Windows
    // closes it on exit, terminating any worker descendants still in the job.
    private static IntPtr handle;
    public static void Attach() {
        if (handle != IntPtr.Zero) return;
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        ExtendedLimits limits = new ExtendedLimits();
        limits.BasicLimitInformation.LimitFlags = 0x00002000; // KILL_ON_JOB_CLOSE
        int size = Marshal.SizeOf(typeof(ExtendedLimits));
        IntPtr memory = Marshal.AllocHGlobal(size);
        try {
            Marshal.StructureToPtr(limits, memory, false);
            if (!SetInformationJobObject(job, 9, memory, (uint)size) ||
                !AssignProcessToJobObject(job, GetCurrentProcess())) {
                int error = Marshal.GetLastWin32Error();
                CloseHandle(job);
                throw new Win32Exception(error);
            }
            handle = job;
        } finally {
            Marshal.FreeHGlobal(memory);
        }
    }
}
'@
    }
    [GardiFundProcessJob]::Attach()
}
