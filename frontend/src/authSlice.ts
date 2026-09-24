import { createAsyncThunk, createSlice } from "@reduxjs/toolkit";
import { api } from "./api";

export interface AuthUser {
  login: string;
  full_name: string | null;
  email: string | null;
  avatar_url: string | null;
  is_admin: boolean;
}

export interface AuthSession {
  enabled: boolean;
  configured: boolean;
  authenticated: boolean;
  user: AuthUser | null;
}

export interface AuthState extends AuthSession {
  status: "idle" | "loading" | "ready" | "error";
  error: string | null;
}

const initialState: AuthState = {
  enabled: true,
  configured: true,
  authenticated: false,
  user: null,
  status: "idle",
  error: null,
};

export const loadSession = createAsyncThunk("auth/loadSession", () =>
  api<AuthSession>("/api/v1/auth/session"),
);

export const logout = createAsyncThunk("auth/logout", () =>
  api<null>("/api/v1/auth/logout", { method: "POST" }),
);

const authSlice = createSlice({
  name: "auth",
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(loadSession.pending, (state) => {
        state.status = "loading";
        state.error = null;
      })
      .addCase(loadSession.fulfilled, (state, action) => {
        Object.assign(state, action.payload, { status: "ready", error: null });
      })
      .addCase(loadSession.rejected, (state, action) => {
        state.status = "error";
        state.error = action.error.message ?? "Unable to check your session";
      })
      .addCase(logout.fulfilled, (state) => {
        state.authenticated = false;
        state.user = null;
        state.status = "ready";
      })
      .addCase(logout.rejected, (state, action) => {
        state.error = action.error.message ?? "Unable to sign out";
      });
  },
});

export default authSlice.reducer;
