package io.minebox.config;

import com.fasterxml.jackson.annotation.JsonProperty;
import io.dropwizard.Configuration;
import io.federecio.dropwizard.swagger.SwaggerBundleConfiguration;

public class ApiConfig extends Configuration {


    public SwaggerBundleConfiguration swagger;

    public MinebdConfig minebd;

}