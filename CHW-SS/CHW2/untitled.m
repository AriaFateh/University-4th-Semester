%==============Load sound file=======================
clear;close all;
load splat;
y = y(1:8192);
%% ============Fourier Transform ===================
Y = fftshift(fft(y));
N = 8192;
fs = 8192;
W = [-pi:2*pi/N:pi-pi/N]*fs;
W
